"""Importa in blocco una lista di testate in feeds.yaml.

Nell'app i feed si aggiungono uno alla volta da Impostazioni; questo script
serve quando la lista e' lunga (decine o centinaia di siti). Per ogni indirizzo cerca il
feed RSS/Atom del sito (src/scoperta_feed.py), lo prova davvero e scarta i
duplicati: piu' pagine dello stesso sito ("/category/space", "/news") spesso
portano allo stesso feed.

Uso:
    python importa_feed.py lista.txt        # un URL per riga
    python importa_feed.py lista.txt --sostituisci   # butta i feed attuali

Gli URL possono anche essere separati da virgole o virgolette: il file viene
letto cercando tutto cio' che somiglia a un indirizzo http.
"""

from __future__ import annotations

import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.config import Feed, carica_feeds, salva_feeds
from src.feed_scoperta import scopri_feed

_URL = re.compile(r"https?://[^\s\"',<>]+")
_LAVORATORI = 12


def _nome_leggibile(titolo: str, url_feed: str) -> str:
    """Il titolo dichiarato dal feed, ripulito dalle code inutili ("... - RSS
    Feed"). Se il feed non ha titolo, il dominio."""
    nome = re.sub(r"\s*[-|:]\s*(rss|feed|atom)([\s\w]*)$", "", titolo, flags=re.IGNORECASE).strip()
    if nome:
        return nome[:80]
    dominio = re.sub(r"^www\.", "", url_feed.split("/")[2])
    return dominio


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    percorso = Path(sys.argv[1])
    sostituisci = "--sostituisci" in sys.argv[2:]
    if not percorso.is_file():
        print(f"File non trovato: {percorso}")
        return 2

    indirizzi = list(dict.fromkeys(_URL.findall(percorso.read_text(encoding="utf-8"))))
    print(f"{len(indirizzi)} indirizzi da provare, {_LAVORATORI} alla volta...\n")

    esistenti = [] if sostituisci else carica_feeds()
    url_presenti = {f.url for f in esistenti}

    nuovi: list[Feed] = []
    doppi: list[tuple[str, str]] = []
    falliti: list[str] = []
    impronte: set[tuple[str, ...]] = set()

    with ThreadPoolExecutor(max_workers=_LAVORATORI) as pool:
        for indirizzo, trovato in zip(indirizzi, pool.map(scopri_feed, indirizzi)):
            if trovato is None:
                falliti.append(indirizzo)
                print(f"  NO   {indirizzo}")
                continue
            # Doppione per URL identico o, piu' spesso, per contenuto: pagine
            # diverse dello stesso sito portano quasi sempre al feed generale.
            if trovato.url in url_presenti or trovato.impronta in impronte:
                doppi.append((indirizzo, trovato.url))
                print(f"  ==   {indirizzo}  ->  {trovato.url} (doppione)")
                continue
            url_presenti.add(trovato.url)
            impronte.add(trovato.impronta)
            nome = _nome_leggibile(trovato.titolo, trovato.url)
            nuovi.append(Feed(nome=nome, url=trovato.url))
            print(f"  OK   {indirizzo}  ->  {trovato.url}  [{nome}]")

    salva_feeds(esistenti + nuovi)

    print(
        f"\nFeed aggiunti: {len(nuovi)} | duplicati o gia' presenti: {len(doppi)} | "
        f"senza feed: {len(falliti)}"
    )
    print(f"Totale in feeds.yaml: {len(esistenti) + len(nuovi)}")
    if falliti:
        print("\nSenza feed RSS utilizzabile:")
        for indirizzo in falliti:
            print(f"  - {indirizzo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
