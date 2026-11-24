import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import agy_resume


def make_summary_db(base_dir: Path, rows: list[tuple]) -> None:
    db_path = base_dir / "conversation_summaries.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE conversation_summaries (
                conversation_id text PRIMARY KEY,
                title text NOT NULL DEFAULT "",
                preview text NOT NULL DEFAULT "",
                step_count integer NOT NULL DEFAULT 0,
                last_modified_time datetime NOT NULL,
                workspace_uris text NOT NULL,
                app_data_dir text NOT NULL DEFAULT "",
                parent_conversation_id text NOT NULL DEFAULT "",
                killed numeric NOT NULL DEFAULT 0
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO conversation_summaries (
                conversation_id, title, preview, step_count,
                last_modified_time, workspace_uris, app_data_dir,
                parent_conversation_id, killed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()


class AgyResumeTests(unittest.TestCase):
    def test_uri_to_norm_path_handles_windows_variants(self) -> None:
        expected = agy_resume.normalize_win_path(r"C:\Users\Jane Doe\workspace")
        self.assertEqual(
            agy_resume.uri_to_norm_path("file:///C:/Users/Jane%20Doe/workspace"),
            expected,
        )
        self.assertEqual(
            agy_resume.uri_to_norm_path("file:///c%3A/Users/Jane%20Doe/workspace/"),
            expected,
        )

    def test_sanitize_text_strips_ansi_and_control_chars(self) -> None:
        dirty = "Ola\x1b[31m\r\nmundo\x00\t!"
        self.assertEqual(agy_resume.sanitize_text(dirty), "Ola [31m mundo !")

    def test_load_conversations_filters_cli_only_existing_db_and_reads_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "conversations").mkdir()
            (base / "annotations").mkdir()

            # Conversa válida da CLI com anotação customizada
            (base / "conversations" / "11111111-1111-1111-1111-111111111111.db").write_bytes(b"")
            (base / "annotations" / "11111111-1111-1111-1111-111111111111.pbtxt").write_text(
                'title:"Titulo Renomeado"', encoding="utf-8"
            )
            # Conversa válida da CLI sem anotação
            (base / "conversations" / "22222222-2222-2222-2222-222222222222.db").write_bytes(b"")
            # Subagente (deve ser ignorado)
            (base / "conversations" / "33333333-3333-3333-3333-333333333333.db").write_bytes(b"")
            # Conversa do App desktop (app_data_dir='antigravity', deve ser ignorada)
            (base / "conversations" / "44444444-4444-4444-4444-444444444444.db").write_bytes(b"")
            # Conversa com ID malicioso (path traversal / flag injection)
            # Conversa deletada (sem .db em conversations/)

            make_summary_db(
                base,
                [
                    (
                        "11111111-1111-1111-1111-111111111111",
                        "Titulo Antigo",
                        "prev 1",
                        10,
                        "2026-11-24 19:07:02.5874704+00:00",
                        '["file:///C:/Users/Jane%20Doe/workspace"]',
                        "antigravity-cli",
                        "",
                        0,
                    ),
                    (
                        "22222222-2222-2222-2222-222222222222",
                        "",
                        "Primeira pergunta",
                        3,
                        "2026-11-24 18:00:00+00:00",
                        '["file:///C:/Users/Jane%20Doe"]',
                        "antigravity-cli",
                        "",
                        0,
                    ),
                    (
                        "33333333-3333-3333-3333-333333333333",
                        "Subagente",
                        "",
                        2,
                        "2026-11-24 19:10:00+00:00",
                        '["file:///C:/Users/Jane%20Doe/workspace"]',
                        "antigravity-cli",
                        "11111111-1111-1111-1111-111111111111",
                        0,
                    ),
                    (
                        "44444444-4444-4444-4444-444444444444",
                        "App Desktop",
                        "",
                        5,
                        "2026-11-24 19:20:00+00:00",
                        '["file:///C:/Users/Jane%20Doe/workspace"]',
                        "antigravity",
                        "",
                        0,
                    ),
                    (
                        "55555555-5555-5555-5555-555555555555",
                        "Deletada",
                        "",
                        5,
                        "2026-11-24 19:30:00+00:00",
                        '["file:///C:/Users/Jane%20Doe/workspace"]',
                        "antigravity-cli",
                        "",
                        0,
                    ),
                    (
                        "--evil-flag",
                        "Injecao",
                        "",
                        1,
                        "2026-11-24 19:40:00+00:00",
                        '["file:///C:/Users/Jane%20Doe/workspace"]',
                        "antigravity-cli",
                        "",
                        0,
                    ),
                ],
            )

            items = agy_resume.load_conversations(base)
            self.assertEqual([i.conversation_id for i in items], [
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
            ])
            self.assertEqual(items[0].title, "Titulo Renomeado")
            self.assertEqual(items[1].title, "Primeira pergunta")

            # Filtro por pasta
            ws_items = agy_resume.filter_conversations(
                items, r"C:\Users\Jane Doe\workspace", show_all=False, query=""
            )
            self.assertEqual(len(ws_items), 1)
            self.assertEqual(ws_items[0].conversation_id, "11111111-1111-1111-1111-111111111111")

            # Busca sem acento e case-insensitive em todas as pastas
            found = agy_resume.filter_conversations(
                items, r"C:\Users\Jane Doe\workspace", show_all=True, query="título"
            )
            self.assertEqual([i.conversation_id for i in found], ["11111111-1111-1111-1111-111111111111"])

    def test_format_relative_time_uses_injected_clock(self) -> None:
        tz = timezone(timedelta(hours=-3))
        now = datetime(2026, 11, 24, 16, 10, 0, tzinfo=tz)
        self.assertEqual(agy_resume.format_relative_time(now - timedelta(seconds=30), now), "agora")
        self.assertEqual(agy_resume.format_relative_time(now - timedelta(minutes=12), now), "há 12 min")
        self.assertEqual(agy_resume.format_relative_time(now - timedelta(hours=2), now), "hoje 14:10")
        self.assertEqual(
            agy_resume.format_relative_time(now - timedelta(days=1, hours=1), now),
            "ontem 15:10",
        )
        self.assertEqual(
            agy_resume.format_relative_time(now - timedelta(days=5), now),
            "19/11 16:10",
        )

    def test_build_agy_command_rejects_flag_injection(self) -> None:
        exe = Path(r"C:\agy\agy.exe")
        self.assertEqual(
            agy_resume.build_agy_command(exe, ["--dangerously-skip-permissions"], "abc-123"),
            [str(exe), "--dangerously-skip-permissions", "--conversation", "abc-123"],
        )
        self.assertEqual(
            agy_resume.build_agy_command(exe, ["--dangerously-skip-permissions"], None),
            [str(exe), "--dangerously-skip-permissions"],
        )
        with self.assertRaises(ValueError):
            agy_resume.build_agy_command(exe, [], "../escape")
        with self.assertRaises(ValueError):
            agy_resume.build_agy_command(exe, [], "--evil-flag")

    def test_render_picker_empty_states_distinguish_filter_vs_empty(self) -> None:
        tz = timezone(timedelta(hours=-3))
        now = datetime(2026, 11, 24, 16, 10, 0, tzinfo=tz)
        frame_filter, _ = agy_resume.render_picker(
            items=[],
            selected=0,
            scroll=0,
            query="naoexiste",
            show_all=False,
            cwd=r"C:\Users\Jane Doe\workspace",
            home=r"C:\Users\Jane Doe",
            total_in_scope=2,
            total_all=3,
            now=now,
            term_size=(100, 20),
        )
        self.assertIn("Esc limpa a busca", frame_filter)

        frame_empty_dir, _ = agy_resume.render_picker(
            items=[],
            selected=0,
            scroll=0,
            query="",
            show_all=False,
            cwd=r"C:\Outro",
            home=r"C:\Users\Jane Doe",
            total_in_scope=0,
            total_all=3,
            now=now,
            term_size=(100, 20),
        )
        self.assertIn("Nenhuma conversa nesta pasta", frame_empty_dir)


if __name__ == "__main__":
    unittest.main()
