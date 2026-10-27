#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["textual>=1.0"]
# ///
"""Abo-Manager: Abos im Terminal überblicken, Preisänderungen und Kombis gegenrechnen.

Start: `uv run abo.py` (Daten in abos.json neben dem Skript, anderer Ort via ABO_DATEI=...).

Kombi = Abo mit Mitgliedern. Aktive Kombi → Mitglieder zählen nicht mehr einzeln.
Inaktive Kombi → reine Idee, zeigt nur, was sie sparen würde.
"""

import calendar
import json
import os
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from rich.text import Text
from textual.app import App
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Label, Static

TURNUS = {1: "monatlich", 3: "quartalsw.", 6: "halbjährl.", 12: "jährlich"}
AKTIONEN = {"neu", "bearbeiten", "preis", "umschalten", "loeschen", "budget"}


# --- Daten & Rechnen ---------------------------------------------------------

def laden(datei):
    daten = json.loads(datei.read_text(encoding="utf-8")) if datei.exists() else {}
    daten.setdefault("budget", 0)
    for a in daten.setdefault("abos", []):
        a.setdefault("mitglieder", [])
        a.setdefault("aktiv", True)
    return daten


def speichern(datei, daten):
    tmp = datei.with_suffix(".tmp")
    tmp.write_text(json.dumps(daten, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(datei)


def eur(x):
    return f"{x:,.2f} €".replace(",", "_").replace(".", ",").replace("_", ".")


def zahl(s, art=float):
    try:
        x = art(s.replace(",", "."))
    except ValueError:
        raise ValueError(f"Keine gültige Zahl: {s!r}") from None
    if x < 0:
        raise ValueError(f"Darf nicht negativ sein: {s!r}")
    return x


def datum(s):
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Datum nicht erkannt: {s!r} (TT.MM.JJJJ)")


def plus_monate(d, n):
    m = d.month - 1 + n
    j, m = d.year + m // 12, m % 12 + 1
    return d.replace(year=j, month=m, day=min(d.day, calendar.monthrange(j, m)[1]))


def preis_eintrag(abo, tag):
    """Der am Tag gültige Preis-Eintrag (vor Abo-Start: der erste)."""
    gueltig = [p for p in abo["preise"] if p["ab"] <= tag.isoformat()]
    return gueltig[-1] if gueltig else abo["preise"][0]


def preis_am(abo, tag):
    return preis_eintrag(abo, tag)["betrag"]


def monatlich(abo, tag):
    return preis_am(abo, tag) / abo["intervall"]


def naechste_zahlung(abo, tag):
    start, n = date.fromisoformat(abo["preise"][0]["ab"]), 0
    while (d := plus_monate(start, n * abo["intervall"])) < tag:
        n += 1
    return d


def abgedeckt(daten):
    """Abo-Name → Name der aktiven Kombi, über die es bezahlt wird."""
    return {m: k["name"] for k in daten["abos"] if k["aktiv"] for m in k["mitglieder"]}


def gezaehlt(daten):
    """Abos, die wirklich Geld kosten: aktiv und nicht in einer aktiven Kombi enthalten."""
    drin = abgedeckt(daten)
    return [a for a in daten["abos"] if a["aktiv"] and a["name"] not in drin]


def kombi_ersparnis(daten, kombi, tag):
    """Summe der Einzelpreise minus Kombipreis pro Monat; negativ = Kombi ist teurer."""
    einzeln = sum(monatlich(a, tag) for a in daten["abos"] if a["name"] in kombi["mitglieder"])
    return einzeln - monatlich(kombi, tag)


def prozent(neu, alt):
    return f"{(neu / alt - 1) * 100:+.0f} %" if alt else "neu"


def entwicklung(abo, tag):
    jetzt, erst = preis_am(abo, tag), abo["preise"][0]
    if kommend := [p for p in abo["preise"][1:] if p["ab"] > tag.isoformat()]:
        p = kommend[0]
        pfeil = "↑" if p["betrag"] > jetzt else "↓"
        return Text(f"{pfeil} {eur(p['betrag'])} ab {date.fromisoformat(p['ab']):%d.%m.%y} ({prozent(p['betrag'], jetzt)})",
                    "yellow")
    if erst["betrag"] != jetzt:
        return Text(f"{prozent(jetzt, erst['betrag'])} seit {date.fromisoformat(erst['ab']):%m/%Y}",
                    "red" if jetzt > erst["betrag"] else "green")
    return Text("")


def zeile(abo, tag, drin, zaehlt):
    if abo["name"] in drin:
        status = f"in {drin[abo['name']]}"
    elif abo["mitglieder"]:
        status = "Kombi" if abo["aktiv"] else "Kombi-Idee"
    else:
        status = "aktiv" if abo["aktiv"] else "inaktiv"
    m, stil = monatlich(abo, tag), "" if zaehlt else "dim"
    zellen = [abo["name"], abo["kategorie"], eur(preis_am(abo, tag)),
              TURNUS.get(abo["intervall"], f"alle {abo['intervall']} M."), eur(m), eur(m * 12),
              f"{naechste_zahlung(abo, tag):%d.%m.%Y}" if zaehlt else "–", status]
    return [Text(z, stil, justify="right" if i in (2, 4, 5) else "left") for i, z in enumerate(zellen)] + [
        entwicklung(abo, tag)]


def zusammenfassung(daten, tag):
    if not daten["abos"]:
        return Text("Noch keine Abos – mit a das erste anlegen.", "dim")
    zaehlt = gezaehlt(daten)
    monat = sum(monatlich(a, tag) for a in zaehlt)
    t = Text(f"{len(zaehlt)} zahlende Abos: {eur(monat)} / Monat · {eur(monat * 12)} / Jahr", "bold")
    if budget := daten["budget"]:
        rest = budget - monat
        t.append(f"   Budget {eur(budget)} → ")
        t.append(f"{eur(rest)} frei" if rest >= 0 else f"{eur(-rest)} drüber", "green" if rest >= 0 else "bold red")
    spaeter = sum(monatlich(a, date.max) for a in zaehlt)
    if abs(spaeter - monat) >= 0.005:
        t.append(f"\nNach angekündigten Preisänderungen: {eur(spaeter)} / Monat "
                 f"({'+' if spaeter > monat else '−'}{eur(abs(spaeter - monat) * 12)} / Jahr)", "yellow")
    kategorien = defaultdict(float)
    for a in zaehlt:
        kategorien[a["kategorie"]] += monatlich(a, tag)
    t.append("\n" + " · ".join(f"{k} {eur(v)}" for k, v in sorted(kategorien.items(), key=lambda kv: -kv[1])), "dim")
    for k in daten["abos"]:
        if k["mitglieder"]:
            diff = kombi_ersparnis(daten, k, tag)
            urteil = f"spart {eur(diff)}" if diff >= 0 else f"kostet {eur(-diff)} mehr"
            t.append(f"\nKombi {k['name']}{'' if k['aktiv'] else ' (nicht gebucht)'}: {urteil} / Monat "
                     f"bzw. {eur(abs(diff) * 12)} / Jahr ggü. Einzelpreisen", "green" if diff >= 0 else "red")
    return t


def abo_felder(abo=None):
    heute = f"{date.today():%d.%m.%Y}"
    if not abo:
        return [("name", "Name", ""), ("kategorie", "Kategorie", ""), ("preis", "Preis in €", ""),
                ("intervall", "Abrechnung alle … Monate (1, 3, 12 …)", "1"), ("start", "Start (TT.MM.JJJJ)", heute),
                ("mitglieder", "Kombi aus (Namen kommagetrennt, leer = normales Abo)", "")]
    return [("name", "Name", abo["name"]), ("kategorie", "Kategorie", abo["kategorie"]),
            ("preis", "Aktueller Preis in € (Korrektur – Erhöhungen über p)",
             f"{preis_am(abo, date.today()):.2f}".replace(".", ",")),
            ("intervall", "Abrechnung alle … Monate (1, 3, 12 …)", str(abo["intervall"])),
            ("start", "Start (TT.MM.JJJJ)", f"{date.fromisoformat(abo['preise'][0]['ab']):%d.%m.%Y}"),
            ("mitglieder", "Kombi aus (Namen kommagetrennt, leer = normales Abo)", ", ".join(abo["mitglieder"]))]


def abo_pruefer(daten, alt=None):
    def pruefen(w):
        if not (name := w["name"]):
            raise ValueError("Name fehlt")
        namen = {a["name"] for a in daten["abos"]} - {alt}
        if name in namen:
            raise ValueError(f"„{name}“ gibt es schon")
        mitglieder = [m.strip() for m in w["mitglieder"].split(",") if m.strip()]
        if fremd := set(mitglieder) - namen:
            raise ValueError(f"Unbekannt in Kombi: {', '.join(sorted(fremd))}")
        if not (intervall := zahl(w["intervall"], int)):
            raise ValueError("Abrechnung mindestens alle 1 Monat")
        return {"name": name, "kategorie": w["kategorie"] or "Sonstiges", "preis": zahl(w["preis"]),
                "intervall": intervall, "start": datum(w["start"]).isoformat(), "mitglieder": mitglieder}

    return pruefen


# --- UI ----------------------------------------------------------------------

class Formular(ModalScreen):
    """Ein Dialog für alles: Felder rein, geprüftes Ergebnis raus (Esc → None)."""
    BINDINGS = [("escape", "abbrechen", "Abbrechen"), ("enter", "ok", "OK")]

    def __init__(self, titel, felder, pruefen):
        super().__init__()
        self.titel, self.felder, self.pruefen = titel, felder, pruefen

    def compose(self):
        with Vertical(id="box"):
            yield Label(self.titel, id="titel", markup=False)
            for key, label, wert in self.felder:
                yield Label(label)
                yield Input(value=wert, id=key)
            yield Label("Enter = OK · Esc = Abbrechen", classes="hilfe")

    def on_input_submitted(self, _):
        self.action_ok()

    def action_ok(self):
        try:
            self.dismiss(self.pruefen({i.id: i.value.strip() for i in self.query(Input)}))
        except ValueError as e:
            self.notify(str(e), severity="error")

    def action_abbrechen(self):
        self.dismiss(None)


class AboApp(App):
    TITLE = "Abo-Manager"
    CSS = """
    DataTable { height: 1fr; }
    #summe { height: auto; padding: 0 1; border-top: solid $primary; }
    Formular { align: center middle; }
    #box { width: 72; height: auto; max-height: 100%; overflow-y: auto; padding: 1 2;
           border: thick $primary; background: $surface; }
    #titel { text-style: bold; margin-bottom: 1; }
    .hilfe { text-style: dim; margin-top: 1; }
    """
    BINDINGS = [("a", "neu", "Neu"), ("e", "bearbeiten", "Bearbeiten"), ("p", "preis", "Preisänderung"),
                ("space", "umschalten", "Aktiv an/aus"), ("d", "loeschen", "Löschen"), ("b", "budget", "Budget"),
                ("q", "quit", "Beenden")]

    def __init__(self, datei):
        super().__init__()
        self.datei = datei
        self.daten = laden(datei)

    def compose(self):
        yield Header()
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Static(id="summe")
        yield Footer()

    def on_mount(self):
        self.sub_title = str(self.datei)
        self.query_one(DataTable).add_columns("Name", "Kategorie", "Preis", "Turnus", "€ / Monat", "€ / Jahr",
                                              "Nächste Zahlung", "Status", "Preisentwicklung")
        self.neu_zeichnen()

    def check_action(self, action, parameters):
        # Haupt-Aktionen nicht unter einem offenen Dialog auslösen
        return not (action in AKTIONEN and isinstance(self.screen, ModalScreen))

    def ausgewaehlt(self):
        t = self.query_one(DataTable)
        if t.row_count:
            key = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
            return next((a for a in self.daten["abos"] if a["name"] == key), None)

    def neu_zeichnen(self, name=None):
        if name is None and (a := self.ausgewaehlt()):
            name = a["name"]
        tag, tabelle = date.today(), self.query_one(DataTable)
        drin, zaehlt = abgedeckt(self.daten), {a["name"] for a in gezaehlt(self.daten)}
        tabelle.clear()
        for a in sorted(self.daten["abos"], key=lambda a: (a["name"] not in zaehlt, -monatlich(a, tag))):
            tabelle.add_row(*zeile(a, tag, drin, a["name"] in zaehlt), key=a["name"])
        if name in {a["name"] for a in self.daten["abos"]}:
            tabelle.move_cursor(row=tabelle.get_row_index(name))
        self.query_one("#summe", Static).update(zusammenfassung(self.daten, tag))

    def sichern(self, name=None):
        speichern(self.datei, self.daten)
        self.neu_zeichnen(name)

    def on_data_table_row_selected(self, _):
        self.action_bearbeiten()

    def action_neu(self):
        def fertig(w):
            if w:
                self.daten["abos"].append({"name": w["name"], "kategorie": w["kategorie"], "intervall": w["intervall"],
                                           "aktiv": True, "mitglieder": w["mitglieder"],
                                           "preise": [{"ab": w["start"], "betrag": w["preis"]}]})
                self.sichern(w["name"])

        self.push_screen(Formular("Neues Abo", abo_felder(), abo_pruefer(self.daten)), fertig)

    def action_bearbeiten(self):
        if not (a := self.ausgewaehlt()):
            return

        def fertig(w):
            if not w:
                return
            for k in self.daten["abos"]:
                k["mitglieder"] = [w["name"] if m == a["name"] else m for m in k["mitglieder"]]
            preis_eintrag(a, date.today())["betrag"] = w["preis"]
            a["preise"][0]["ab"] = w["start"]
            a["preise"].sort(key=lambda p: p["ab"])
            a.update(name=w["name"], kategorie=w["kategorie"], intervall=w["intervall"], mitglieder=w["mitglieder"])
            self.sichern(w["name"])

        self.push_screen(Formular(f"Bearbeiten: {a['name']}", abo_felder(a), abo_pruefer(self.daten, a["name"])),
                         fertig)

    def action_preis(self):
        if not (a := self.ausgewaehlt()):
            return

        def fertig(w):
            if w:
                a["preise"] = sorted([p for p in a["preise"] if p["ab"] != w["ab"]] + [w], key=lambda p: p["ab"])
                self.sichern(a["name"])

        felder = [("betrag", "Neuer Preis in €", ""), ("ab", "Gültig ab (TT.MM.JJJJ)", f"{date.today():%d.%m.%Y}")]
        self.push_screen(Formular(f"Preisänderung: {a['name']}", felder,
                                  lambda w: {"ab": datum(w["ab"]).isoformat(), "betrag": zahl(w["betrag"])}), fertig)

    def action_umschalten(self):
        if a := self.ausgewaehlt():
            a["aktiv"] = not a["aktiv"]
            self.sichern(a["name"])

    def action_loeschen(self):
        if not (a := self.ausgewaehlt()):
            return

        def fertig(w):
            if w is not None:
                self.daten["abos"].remove(a)
                for k in self.daten["abos"]:
                    k["mitglieder"] = [m for m in k["mitglieder"] if m != a["name"]]
                self.sichern()

        self.push_screen(Formular(f"„{a['name']}“ wirklich löschen?", [], lambda w: w), fertig)

    def action_budget(self):
        def fertig(w):
            if w:
                self.daten["budget"] = w["budget"]
                self.sichern()

        wert = f"{self.daten['budget']:.2f}".replace(".", ",") if self.daten["budget"] else ""
        self.push_screen(Formular("Monatsbudget für Abos", [("budget", "Betrag in € (leer = kein Budget)", wert)],
                                  lambda w: {"budget": zahl(w["budget"]) if w["budget"] else 0}), fertig)


if __name__ == "__main__":
    AboApp(Path(os.environ.get("ABO_DATEI", Path(__file__).with_name("abos.json")))).run()
