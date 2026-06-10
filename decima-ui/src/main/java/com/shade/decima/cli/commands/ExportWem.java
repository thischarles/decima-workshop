package com.shade.decima.cli.commands;

import com.shade.decima.model.app.Project;
import com.shade.decima.model.packfile.Packfile;
import com.shade.decima.model.packfile.PackfileManager;
import com.shade.decima.model.rtti.RTTICoreFile;
import com.shade.decima.model.rtti.RTTICoreFileReader.LoggingErrorHandlingStrategy;
import com.shade.decima.model.rtti.objects.RTTIObject;
import com.shade.decima.model.rtti.types.java.HwDataSource;
import com.shade.platform.model.util.IOUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;
import picocli.CommandLine.Parameters;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

@Command(name = "export-wem", description = "Extracts streamed WEM data from WwiseBankResource(s) in a core, by source id", sortOptions = false)
public class ExportWem implements Runnable {
    private static final Logger log = LoggerFactory.getLogger(ExportWem.class);

    @Option(names = {"-p", "--project"}, required = true, description = "The working project")
    private Project project;

    @Option(names = {"-o", "--output"}, required = true, description = "The output directory")
    private Path output;

    @Option(names = {"-c", "--core"}, required = true, description = "Core path containing WwiseBankResource(s)")
    private String corePath;

    @Parameters(description = "Source (WEM) IDs to extract")
    private List<Long> sourceIds;

    @Override
    public void run() {
        try {
            final PackfileManager manager = project.getPackfileManager();
            final Packfile packfile = manager.findFirst(corePath);

            if (packfile == null) {
                log.error("Core not found: {}", corePath);
                return;
            }

            Files.createDirectories(output);

            final long hash = Packfile.getPathHash(Packfile.getNormalizedPath(corePath));
            final RTTICoreFile core = project.getCoreFileReader()
                .read(packfile.getFile(hash), LoggingErrorHandlingStrategy.getInstance());

            core.visitAllObjects("WwiseBankResource", bank -> {
                final int[] wemIDs = bank.get("WemIDs");
                final RTTIObject[] dataSources = bank.objs("DataSources");

                for (Long sid : sourceIds) {
                    final int index = IOUtils.indexOf(wemIDs, sid.intValue());

                    if (index < 0) {
                        continue;
                    }

                    try {
                        final byte[] data = dataSources[index].<HwDataSource>cast().getData(manager);
                        final Path out = output.resolve(Integer.toUnsignedString(sid.intValue()) + ".wem");
                        Files.write(out, data);
                        log.info("Wrote {} ({} bytes)", out, data.length);
                    } catch (Exception e) {
                        log.error("Failed to extract source {}: {}", sid, e.toString());
                    }
                }
            });
        } catch (Exception e) {
            throw new RuntimeException("export-wem failed", e);
        }
    }
}
