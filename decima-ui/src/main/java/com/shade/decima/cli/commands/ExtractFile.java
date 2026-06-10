package com.shade.decima.cli.commands;

import com.shade.decima.model.app.Project;
import com.shade.decima.model.packfile.Packfile;
import com.shade.decima.model.packfile.PackfileManager;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;
import picocli.CommandLine.Parameters;

import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static java.nio.file.StandardOpenOption.*;

@Command(name = "extract", description = "Extracts raw (decompressed) files from the project's archives to a directory", sortOptions = false)
public class ExtractFile implements Runnable {
    private static final Logger log = LoggerFactory.getLogger(ExtractFile.class);

    @Option(names = {"-p", "--project"}, required = true, description = "The working project")
    private Project project;

    @Option(names = {"-o", "--output"}, required = true, description = "The output directory")
    private Path output;

    @Parameters(description = "One or more file paths to extract")
    private List<String> paths;

    @Override
    public void run() {
        final PackfileManager manager = project.getPackfileManager();

        try {
            Files.createDirectories(output);

            for (String path : paths) {
                final Packfile packfile = manager.findFirst(path);

                if (packfile == null) {
                    log.warn("Not found: {}", path);
                    continue;
                }

                final String name = path.substring(path.lastIndexOf('/') + 1);
                final Path target = output.resolve(name);

                try (
                    InputStream is = packfile.newInputStream(path);
                    OutputStream os = Files.newOutputStream(target, CREATE, WRITE, TRUNCATE_EXISTING)
                ) {
                    final long size = is.transferTo(os);
                    log.info("Wrote {} ({} bytes)", target, size);
                }
            }
        } catch (Exception e) {
            throw new RuntimeException("Error extracting files", e);
        }
    }
}
