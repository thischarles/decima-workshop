package com.shade.decima.model.viewer;

import com.formdev.flatlaf.util.UIScale;
import com.shade.decima.model.viewer.renderer.DebugRenderer;
import com.shade.decima.model.viewer.renderer.GridRenderer;
import com.shade.decima.model.viewer.renderer.ModelRenderer;
import com.shade.decima.model.viewer.renderer.OutlineRenderer;
import com.shade.platform.model.Disposable;
import com.shade.platform.model.util.MathUtils;
import com.shade.util.NotNull;
import com.shade.util.Nullable;
import org.joml.Vector2f;
import org.joml.Vector3f;
import org.lwjgl.BufferUtils;
import org.lwjgl.PointerBuffer;
import org.lwjgl.opengl.GL;
import org.lwjgl.opengl.GL11;
import org.lwjgl.opengl.GL12;
import org.lwjgl.opengl.GL14;
import org.lwjgl.opengl.GL30;
import org.lwjgl.system.MemoryStack;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import javax.swing.*;
import java.awt.*;
import java.awt.event.FocusEvent;
import java.awt.event.FocusListener;
import java.awt.event.KeyEvent;
import java.awt.event.KeyListener;
import java.awt.event.MouseAdapter;
import java.awt.event.MouseEvent;
import java.awt.event.MouseWheelEvent;
import java.awt.image.BufferedImage;
import java.awt.image.DataBufferInt;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.IntBuffer;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

import static org.lwjgl.opengl.CGL.*;

/**
 * A lightweight, macOS-only replacement for the heavyweight {@link ModelViewport} canvas.
 * <p>
 * On macOS the AWT heavyweight OpenGL canvas is backed by a layer-backed {@code NSOpenGLView}
 * that draws <i>over</i> lightweight Swing siblings and doesn't clip to tab/split boundaries,
 * which causes the 3D preview to bleed across split views. To avoid that, this component never
 * puts an {@code NSOpenGLView} in the UI at all: it creates a standalone offscreen OpenGL (CGL)
 * context on its own thread, renders the scene into a framebuffer object, reads the pixels back,
 * and paints them into a normal lightweight component that composites and clips like any other.
 * <p>
 * It does not own any view state. A non-displayed {@link ModelViewport} is used as the
 * <i>delegate</i> that holds the camera, render flags (wireframe/normals/shading), the outline
 * selection, and the current model, so the existing renderers, menus, and {@code NodeModel}
 * keep working unchanged. This surface only adds the offscreen GL context, input, and display.
 * <p>
 * This breaks render-path parity with Windows/Linux (which keep the heavyweight canvas), so it's
 * deliberately gated to macOS in {@link ModelViewerPanel}.
 */
final class OffscreenModelSurface extends JComponent implements Disposable {
    private static final Logger log = LoggerFactory.getLogger(OffscreenModelSurface.class);

    private final ModelViewport delegate;
    private final Camera camera;
    private final InputHandler input;
    private final RenderThread thread;
    private final Consumer<String> statusConsumer;

    // Latest finished frame, published from the render thread and drawn on the EDT.
    private volatile BufferedImage frame;

    // Model hand-off (set on the EDT, applied on the render thread).
    private volatile Model pendingModel;
    private volatile boolean modelDirty;

    OffscreenModelSurface(@NotNull ModelViewport delegate, @Nullable Consumer<String> statusConsumer) {
        this.delegate = delegate;
        this.camera = delegate.getCamera();
        this.statusConsumer = statusConsumer;

        Robot robot = null;
        try {
            robot = new Robot();
        } catch (AWTException e) {
            log.warn("Can't create robot", e);
        }
        this.input = new InputHandler(robot);

        setOpaque(true);
        setBackground(Color.BLACK);
        setFocusable(true);

        addMouseListener(input);
        addMouseMotionListener(input);
        addMouseWheelListener(input);
        addKeyListener(input);
        addFocusListener(input);

        // The model is set on the delegate (see ModelViewerPanel#updatePreview); mirror it here.
        delegate.addPropertyChangeListener("model", e -> {
            pendingModel = (Model) e.getNewValue();
            modelDirty = true;
        });

        this.thread = new RenderThread();
        this.thread.start();
    }

    @Override
    protected void paintComponent(Graphics g) {
        final BufferedImage img = frame;
        g.setColor(Color.BLACK);
        g.fillRect(0, 0, getWidth(), getHeight());
        if (img != null) {
            // The image is in device pixels; draw it into the logical bounds so HiDPI stays crisp.
            g.drawImage(img, 0, 0, getWidth(), getHeight(), null);
        }
    }

    @Override
    public void dispose() {
        thread.running.set(false);
        try {
            thread.join(2000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private boolean isThrottling() {
        return !isShowing() || getWidth() <= 0 || getHeight() <= 0;
    }

    private final class RenderThread extends Thread {
        private final AtomicBoolean running = new AtomicBoolean(true);

        private long context;
        private int fbo;
        private int colorTexture;
        private int depthBuffer;
        private int width;
        private int height;
        private IntBuffer pixels;

        private OutlineRenderer outlineRenderer;
        private GridRenderer gridRenderer;
        private ModelRenderer modelRenderer;
        private DebugRenderer debugRenderer;

        private long lastFrameTime;
        private long lastStatusTime;
        private int framesPassed;

        RenderThread() {
            super("Offscreen Render Loop");
            setDaemon(true);
        }

        @Override
        public void run() {
            try {
                createContext();
            } catch (Throwable t) {
                log.error("Failed to create offscreen OpenGL context", t);
                return;
            }

            try {
                setupRenderers();

                while (running.get()) {
                    if (isThrottling()) {
                        sleep(100);
                        continue;
                    }

                    renderFrame();
                    sleep(8);
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            } catch (Throwable t) {
                log.error("Offscreen render loop stopped", t);
            } finally {
                disposeGL();
            }
        }

        private void createContext() {
            try (MemoryStack stack = MemoryStack.stackPush()) {
                // kCGLOGLPVersion_GL4_Core (0x4100); LWJGL only exposes the 3.2 token, and macOS
                // caps OpenGL at 4.1 anyway. 4.1 core is enough for the #version 330 shaders.
                final int kCGLOGLPVersion_GL4_Core = 0x4100;

                final IntBuffer attribs = stack.ints(
                    kCGLPFAOpenGLProfile, kCGLOGLPVersion_GL4_Core,
                    kCGLPFAColorSize, 24,
                    kCGLPFADepthSize, 24,
                    kCGLPFAAccelerated,
                    0
                );

                final PointerBuffer format = stack.mallocPointer(1);
                final IntBuffer count = stack.mallocInt(1);

                int err = CGLChoosePixelFormat(attribs, format, count);
                if (err != 0 || count.get(0) == 0) {
                    throw new IllegalStateException("CGLChoosePixelFormat failed (error " + err + ")");
                }

                final PointerBuffer ctx = stack.mallocPointer(1);
                err = CGLCreateContext(format.get(0), 0L, ctx);
                CGLDestroyPixelFormat(format.get(0));
                if (err != 0) {
                    throw new IllegalStateException("CGLCreateContext failed (error " + err + ")");
                }

                context = ctx.get(0);
            }

            CGLSetCurrentContext(context);
            GL.createCapabilities();
        }

        private void setupRenderers() {
            GL11.glClearColor(0.0f, 0.0f, 0.0f, 0.0f);
            GL11.glEnable(GL11.GL_CULL_FACE);
            GL11.glEnable(GL11.GL_DEPTH_TEST);
            // KHR_debug (GL 4.3) isn't available on macOS (OpenGL is capped at 4.1), so no debug output.

            outlineRenderer = new OutlineRenderer();
            gridRenderer = new GridRenderer();
            modelRenderer = new ModelRenderer();
            debugRenderer = new DebugRenderer();

            try {
                outlineRenderer.setup();
                gridRenderer.setup();
                modelRenderer.setup();
                debugRenderer.setup();
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }

        private void renderFrame() {
            final GraphicsConfiguration gc = getGraphicsConfiguration();
            final double scaleFactor = gc != null ? UIScale.getSystemScaleFactor(gc) : 1.0;
            final int w = Math.max(1, (int) (getWidth() * scaleFactor));
            final int h = Math.max(1, (int) (getHeight() * scaleFactor));

            ensureFramebuffer(w, h);

            if (modelDirty) {
                modelRenderer.setModel(pendingModel);
                modelDirty = false;
            }

            GL30.glBindFramebuffer(GL30.GL_FRAMEBUFFER, fbo);
            GL11.glViewport(0, 0, w, h);
            GL11.glClear(GL11.GL_COLOR_BUFFER_BIT | GL11.GL_DEPTH_BUFFER_BIT);

            final long now = System.currentTimeMillis();
            final float delta = lastFrameTime == 0 ? 0.0f : (now - lastFrameTime) / 1000.0f;

            camera.resize(w, h);
            camera.update(input, delta);
            input.clear();

            outlineRenderer.bind(w, h);

            modelRenderer.setSelectionOnly(true);
            modelRenderer.render(delta, delegate);
            modelRenderer.setSelectionOnly(false);
            modelRenderer.render(delta, delegate);

            gridRenderer.render(delta, delegate);

            if (input.isMouseDown(MouseEvent.BUTTON2) || input.isMouseDown(MouseEvent.BUTTON3)) {
                final Vector3f target = camera.getTarget();
                debugRenderer.cross(target, 0.1f, false);
                debugRenderer.circle(target, camera.getForwardVector(), new Vector3f(1.0f, 1.0f, 0.0f), 0.05f, 8, false);
            }
            debugRenderer.render(delta, delegate);

            // OutlineRenderer#unbind() binds the default framebuffer (0), which has no drawable in
            // this offscreen context, so rebind our FBO before the final composite + read-back.
            outlineRenderer.unbind();
            GL30.glBindFramebuffer(GL30.GL_FRAMEBUFFER, fbo);
            GL11.glViewport(0, 0, w, h);
            outlineRenderer.render(delta, delegate);

            lastFrameTime = now;

            frame = readPixels(w, h);
            repaint();
            reportStatus(now);
        }

        private void ensureFramebuffer(int w, int h) {
            if (fbo != 0 && w == width && h == height) {
                return;
            }

            if (fbo != 0) {
                GL30.glDeleteFramebuffers(fbo);
                GL11.glDeleteTextures(colorTexture);
                GL30.glDeleteRenderbuffers(depthBuffer);
            }

            fbo = GL30.glGenFramebuffers();
            GL30.glBindFramebuffer(GL30.GL_FRAMEBUFFER, fbo);

            colorTexture = GL11.glGenTextures();
            GL11.glBindTexture(GL11.GL_TEXTURE_2D, colorTexture);
            GL11.glTexImage2D(GL11.GL_TEXTURE_2D, 0, GL11.GL_RGBA8, w, h, 0, GL11.GL_RGBA, GL11.GL_UNSIGNED_BYTE, 0L);
            GL11.glTexParameteri(GL11.GL_TEXTURE_2D, GL11.GL_TEXTURE_MIN_FILTER, GL11.GL_LINEAR);
            GL11.glTexParameteri(GL11.GL_TEXTURE_2D, GL11.GL_TEXTURE_MAG_FILTER, GL11.GL_LINEAR);
            GL30.glFramebufferTexture2D(GL30.GL_FRAMEBUFFER, GL30.GL_COLOR_ATTACHMENT0, GL11.GL_TEXTURE_2D, colorTexture, 0);

            depthBuffer = GL30.glGenRenderbuffers();
            GL30.glBindRenderbuffer(GL30.GL_RENDERBUFFER, depthBuffer);
            GL30.glRenderbufferStorage(GL30.GL_RENDERBUFFER, GL14.GL_DEPTH_COMPONENT24, w, h);
            GL30.glFramebufferRenderbuffer(GL30.GL_FRAMEBUFFER, GL30.GL_DEPTH_ATTACHMENT, GL30.GL_RENDERBUFFER, depthBuffer);

            final int status = GL30.glCheckFramebufferStatus(GL30.GL_FRAMEBUFFER);
            if (status != GL30.GL_FRAMEBUFFER_COMPLETE) {
                throw new IllegalStateException("Offscreen framebuffer is incomplete (status " + status + ")");
            }

            width = w;
            height = h;
            pixels = BufferUtils.createIntBuffer(w * h);
        }

        @NotNull
        private BufferedImage readPixels(int w, int h) {
            pixels.clear();
            GL30.glBindFramebuffer(GL30.GL_FRAMEBUFFER, fbo);
            GL30.glReadBuffer(GL30.GL_COLOR_ATTACHMENT0);
            GL11.glPixelStorei(GL11.GL_PACK_ALIGNMENT, 1);
            // BGRA + UNSIGNED_INT_8_8_8_8_REV yields 0xAARRGGBB ints, matching TYPE_INT_ARGB.
            GL11.glReadPixels(0, 0, w, h, GL12.GL_BGRA, GL12.GL_UNSIGNED_INT_8_8_8_8_REV, pixels);

            final BufferedImage image = new BufferedImage(w, h, BufferedImage.TYPE_INT_ARGB);
            final int[] dst = ((DataBufferInt) image.getRaster().getDataBuffer()).getData();

            // OpenGL reads bottom-up; flip vertically into the top-down image.
            for (int y = 0; y < h; y++) {
                pixels.position((h - 1 - y) * w);
                pixels.get(dst, y * w, w);
            }

            return image;
        }

        private void reportStatus(long now) {
            framesPassed += 1;
            if (statusConsumer != null && now - lastStatusTime >= 1000) {
                final String text = "%.3f ms/frame, %d fps".formatted(1000.0 / framesPassed, framesPassed);
                SwingUtilities.invokeLater(() -> statusConsumer.accept(text));
                lastStatusTime = now;
                framesPassed = 0;
            }
        }

        private void disposeGL() {
            if (context == 0) {
                return;
            }

            try {
                if (outlineRenderer != null) {
                    outlineRenderer.dispose();
                }
                if (gridRenderer != null) {
                    gridRenderer.dispose();
                }
                if (modelRenderer != null) {
                    modelRenderer.dispose();
                }
                if (debugRenderer != null) {
                    debugRenderer.dispose();
                }
                if (fbo != 0) {
                    GL30.glDeleteFramebuffers(fbo);
                    GL11.glDeleteTextures(colorTexture);
                    GL30.glDeleteRenderbuffers(depthBuffer);
                }
            } catch (Throwable t) {
                log.warn("Error while disposing offscreen GL resources", t);
            }

            CGLSetCurrentContext(0L);
            CGLDestroyContext(context);
            context = 0;
        }
    }

    /**
     * Mirrors {@code ModelViewport.Handler}: tracks mouse/keyboard state on this (lightweight)
     * component and exposes it to the camera as an {@link InputState}.
     */
    private final class InputHandler extends MouseAdapter implements KeyListener, FocusListener, InputState {
        private final Robot robot;
        // Mouse/keyboard events arrive on the EDT but are read by the render thread, so the state
        // is concurrent and the deltas below are guarded by this handler's monitor.
        private final Set<Integer> mouseState = ConcurrentHashMap.newKeySet();
        private final Set<Integer> keyState = ConcurrentHashMap.newKeySet(3);

        private final Point mouseRecent = new Point();
        private final Point mouseDelta = new Point();
        private float mouseWheelDelta;

        InputHandler(@Nullable Robot robot) {
            this.robot = robot;
        }

        @Override
        public synchronized void mousePressed(MouseEvent e) {
            requestFocusInWindow();
            mouseState.add(e.getButton());
            mouseRecent.setLocation(e.getPoint());
            mouseDelta.setLocation(0, 0);
            SwingUtilities.convertPointToScreen(mouseRecent, OffscreenModelSurface.this);
        }

        @Override
        public void mouseReleased(MouseEvent e) {
            mouseState.remove(e.getButton());
        }

        @Override
        public synchronized void mouseDragged(MouseEvent e) {
            final Point mouse = e.getLocationOnScreen();
            final Rectangle bounds = new Rectangle(getLocationOnScreen(), getSize());

            // Shrink the bounds in case the window is maximized so the mouse can move out of bounds there
            bounds.width -= 1;
            bounds.height -= 1;

            if (robot != null && !bounds.contains(mouse)) {
                mouse.x = MathUtils.wrapAround(mouse.x, bounds.x, bounds.x + bounds.width);
                mouse.y = MathUtils.wrapAround(mouse.y, bounds.y, bounds.y + bounds.height);

                robot.mouseMove(mouse.x, mouse.y);
                mouseRecent.setLocation(mouse.x, mouse.y);
            } else {
                mouseDelta.x += mouse.x - mouseRecent.x;
                mouseDelta.y += mouse.y - mouseRecent.y;
                mouseRecent.setLocation(mouse);
            }
        }

        @Override
        public synchronized void mouseWheelMoved(MouseWheelEvent e) {
            mouseWheelDelta -= (float) e.getPreciseWheelRotation();
        }

        @Override
        public void keyTyped(KeyEvent e) {
            // do nothing
        }

        @Override
        public void keyPressed(KeyEvent e) {
            keyState.add(e.getKeyCode());
        }

        @Override
        public void keyReleased(KeyEvent e) {
            keyState.remove(e.getKeyCode());
        }

        @Override
        public void focusGained(FocusEvent e) {
            // do nothing
        }

        @Override
        public void focusLost(FocusEvent e) {
            keyState.clear();
            mouseState.clear();
            setCursor(null);
        }

        @Override
        public boolean isKeyDown(int keyCode) {
            return keyState.contains(keyCode);
        }

        @Override
        public boolean isMouseDown(int mouseButton) {
            return mouseState.contains(mouseButton);
        }

        @NotNull
        @Override
        public synchronized Vector2f getMousePositionDelta() {
            return new Vector2f(mouseDelta.x, mouseDelta.y);
        }

        @Override
        public synchronized float getMouseWheelRotationDelta() {
            return mouseWheelDelta;
        }

        private synchronized void clear() {
            mouseDelta.setLocation(0, 0);
            mouseWheelDelta = 0.0f;
        }
    }
}
