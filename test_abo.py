# /// script
# requires-python = ">=3.11"
# dependencies = ["textual>=1.0"]
# ///
"""Self-Check: `uv run test_abo.py`"""

import asyncio
import json
import tempfile
from datetime import date
from pathlib import Path

import abo

T = date(2026, 10, 27)


def neu(name, preise, intervall=1, mitglieder=(), aktiv=True):
    return {"name": name, "kategorie": "X", "intervall": intervall, "aktiv": aktiv, "mitglieder": list(mitglieder),
            "preise": [{"ab": ab, "betrag": b} for ab, b in preise]}


def rechnen():
    assert abo.plus_monate(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert abo.plus_monate(date(2026, 11, 15), 3) == date(2027, 2, 15)
    assert abo.eur(1234.5) == "1.234,50 €"
    n = neu("Netflix", [("2024-01-15", 13.99), ("2026-12-01", 15.99)])
    assert abo.preis_am(n, T) == 13.99 and abo.preis_am(n, date.max) == 15.99
    assert abo.naechste_zahlung(n, T) == date(2026, 11, 15)
    j = neu("Jahr", [("2025-03-01", 120)], intervall=12)
    assert abo.monatlich(j, T) == 10 and abo.naechste_zahlung(j, T) == date(2027, 3, 1)
    d = neu("Disney", [("2025-01-01", 9.99)])
    k = neu("Kombi", [("2025-01-01", 18.99)], mitglieder=["Netflix", "Disney"])
    daten = {"budget": 50, "abos": [n, j, d, k]}
    assert {a["name"] for a in abo.gezaehlt(daten)} == {"Jahr", "Kombi"}
    assert round(abo.kombi_ersparnis(daten, k, T), 2) == 4.99
    k["aktiv"] = False
    assert {a["name"] for a in abo.gezaehlt(daten)} == {"Netflix", "Jahr", "Disney"}


async def ui():
    with tempfile.TemporaryDirectory() as tmp:
        datei = Path(tmp) / "neu" / "abos.json"  # Ordner fehlt noch → speichern legt ihn an
        lesen = lambda: json.loads(datei.read_text())["abos"]
        async with abo.AboApp(datei).run_test(size=(140, 40)) as pilot:
            await pilot.press("a", *"Netflix", "tab", *"Streaming", "tab", *"13,99", "enter")
            await pilot.pause()
            assert lesen()[0]["preise"][0]["betrag"] == 13.99, lesen()
            await pilot.press("p", *"15,99", "enter")  # gleiches Datum → ersetzt
            await pilot.pause()
            assert [p["betrag"] for p in lesen()[0]["preise"]] == [15.99], lesen()
            await pilot.press("a", "enter")  # leerer Name → Fehler, Dialog bleibt offen
            await pilot.pause()
            assert isinstance(pilot.app.screen, abo.Formular)
            await pilot.press("escape", "space")
            await pilot.pause()
            assert lesen()[0]["aktiv"] is False
            await pilot.press("d", "enter")
            await pilot.pause()
            assert lesen() == []


rechnen()
asyncio.run(ui())
print("ok")
