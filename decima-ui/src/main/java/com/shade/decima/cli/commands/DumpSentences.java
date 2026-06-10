package com.shade.decima.cli.commands;

import com.shade.decima.model.app.Project;
import com.shade.decima.model.archive.ArchiveFile;
import com.shade.decima.model.packfile.PackfileManager;
import com.shade.decima.model.rtti.RTTICoreFile;
import com.shade.decima.model.rtti.RTTICoreFileReader.LoggingErrorHandlingStrategy;
import com.shade.decima.model.rtti.RTTIEnum;
import com.shade.decima.model.rtti.RTTIUtils;
import com.shade.decima.model.rtti.objects.RTTIObject;
import com.shade.decima.model.rtti.registry.RTTITypeRegistry;
import com.shade.decima.model.rtti.types.java.HwLocalizedText;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

@Command(name = "sentences", description = "Dumps SentenceResource text + speaker + audio UUID (TSV) from a localization core", sortOptions = false)
public class DumpSentences implements Runnable {
    private static final Logger log = LoggerFactory.getLogger(DumpSentences.class);

    @Option(names = {"-p", "--project"}, required = true, description = "The working project")
    private Project project;

    @Option(names = {"-o", "--output"}, required = true, description = "Output TSV file")
    private Path output;

    @Option(names = {"-c", "--core"}, required = true, description = "Localization core path (sentences.core)")
    private String corePath;

    @Option(names = {"-l", "--language"}, defaultValue = "English", description = "Language for the text")
    private String language;

    @Override
    public void run() {
        try {
            final PackfileManager manager = project.getPackfileManager();
            final RTTITypeRegistry registry = project.getTypeRegistry();
            final int langIndex = ((RTTIEnum) registry.find("ELanguage")).valueOf(language).value() - 1;

            final ArchiveFile file = manager.findFile(corePath);
            if (file == null) {
                log.error("Core not found: {}", corePath);
                return;
            }

            final RTTICoreFile core = project.getCoreFileReader()
                .read(file, LoggingErrorHandlingStrategy.getInstance());

            final List<String> rows = new ArrayList<>();
            rows.add("sentenceUUID\ttextUUID\tspeaker\tgender\ttext");

            for (RTTIObject object : core.objects()) {
                if (!object.type().isInstanceOf("SentenceResource")) {
                    continue;
                }
                try {
                    final RTTIObject text = object.ref("Text").get(project, core);
                    final RTTIObject voice = object.ref("Voice").get(project, core);
                    if (text == null) {
                        continue;
                    }

                    final String t = text.obj("Data").<HwLocalizedText>cast().getTranslation(langIndex);

                    String speaker = "";
                    String gender = "";
                    if (voice != null) {
                        try {
                            gender = voice.str("Gender");
                            speaker = voice.ref("NameResource").get(project, core)
                                .obj("Data").<HwLocalizedText>cast().getTranslation(langIndex);
                        } catch (Exception ignored) {
                        }
                    }

                    rows.add("%s\t%s\t%s\t%s\t%s".formatted(
                        RTTIUtils.uuidToString(object.uuid()),
                        RTTIUtils.uuidToString(text.uuid()),
                        speaker.replace('\t', ' ').replace('\n', ' '),
                        gender,
                        t.replace('\t', ' ').replace('\n', ' ')
                    ));
                } catch (Exception e) {
                    // skip unreadable sentence
                }
            }

            Files.write(output, rows, StandardCharsets.UTF_8);
            log.info("Wrote {} sentences to {}", rows.size() - 1, output);
        } catch (Exception e) {
            throw new RuntimeException("dump-sentences failed", e);
        }
    }
}
