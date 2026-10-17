package io.github.guillaumeboileaupro.controltv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class AcceptanceTraceFilesTest {
    @get:Rule
    val folder = TemporaryFolder()

    private fun line(request: String, event: String, index: Int, command: String? = "pause") =
        """{"sequence":$index,"client":"android","event":"$event","request_id":"$request",""" +
            """"index":$index${if (command == null) "" else ",\"command\":\"$command\""}}"""

    @Test
    fun theEvidenceStaysInTheAppPrivateFilesDirectory() {
        val files = File("/data/user/0/app/files")

        assertEquals(File(files, "acceptance"), AcceptanceTraceFiles.directory(files))
        assertEquals(
            File(files, "acceptance/command-trace.jsonl"),
            AcceptanceTraceFiles.traceFile(files),
        )
    }

    @Test
    fun eachRequestIsSummarizedWithItsClientCommandAndOutcome() {
        val summaries = AcceptanceTraceFiles.summarize(
            listOf(
                line("r1", "command_prepared", 0),
                line("r2", "command_prepared", 0, "play"),
                "not json",
                """{"event":"no request id"}""",
                line("r1", "send_attempt", 1),
                line("r1", "ambiguous_result", 2),
            ),
        )

        assertEquals(
            listOf(
                AcceptanceTraceFiles.RequestSummary("r1", "android", "pause", 3, "ambiguous_result"),
                AcceptanceTraceFiles.RequestSummary("r2", "android", "play", 1, "-"),
            ),
            summaries,
        )
    }

    @Test
    fun theTerminalEventsMatchThePythonTrace() {
        assertEquals(
            setOf(
                "command_confirmed",
                "command_unconfirmed",
                "confirmation_skipped",
                "ambiguous_result",
                "not_sent",
                "command_rejected",
            ),
            AcceptanceTraceFiles.TERMINAL_EVENTS,
        )
    }

    @Test
    fun deletingRemovesEveryAcceptanceFile() {
        val files = folder.newFolder("files")
        val directory = AcceptanceTraceFiles.directory(files)
        File(directory, "gate").mkdirs()
        AcceptanceTraceFiles.traceFile(files).writeText(line("r1", "command_prepared", 0))

        assertTrue(AcceptanceTraceFiles.delete(files))
        assertFalse(directory.exists())
        assertTrue(AcceptanceTraceFiles.delete(files))
    }
}
