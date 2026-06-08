package com.shade.gl.awt;

import org.lwjgl.system.JNI;
import org.lwjgl.system.macosx.ObjCRuntime;

/**
 * MacOSX-only helpers. Vendored from lwjgl3-awt (org.lwjgl.awt.MacOSX, @author SWinxy).
 */
final class MacOSX {
    private static final long objc_msgSend = ObjCRuntime.getLibrary().getFunctionAddress("objc_msgSend");

    private MacOSX() {}

    /** Equivalent of {@code [CATransaction flush];} – flushes the CoreAnimation pipeline. */
    static void caFlush() {
        long CATransaction = ObjCRuntime.objc_getClass("CATransaction");
        long flush = ObjCRuntime.sel_getUid("flush");
        JNI.invokePPP(CATransaction, flush, objc_msgSend);
    }
}
