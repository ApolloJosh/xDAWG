"""
Generate a synthetic xDAWG dataset so the site renders before the real
pipeline has ever run.

THE NUMBERS HERE ARE FABRICATED. They exist to exercise the layout, the
sorting, the filters, and the breakdown panel -- nothing else. The site
carries a visible notice whenever `synthetic` is true in the payload.

Player names and teams are real (illustrative rosters); every metric
attached to them is random.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xdawg.aggregate import compute          # noqa: E402
from xdawg.config import COMPONENTS, TEAMS   # noqa: E402
from xdawg.export import build_payload, write_site_data  # noqa: E402

RNG = np.random.default_rng(20260818)

ROSTER: dict[str, list[str]] = {
    "BAL": ["Gunnar Henderson|SS", "Adley Rutschman|C", "Jordan Westburg|3B", "Colton Cowser|OF", "Ryan Mountcastle|1B", "Grayson Rodriguez|SP", "Felix Bautista|RP", "Zach Eflin|SP"],
    "BOS": ["Rafael Devers|3B", "Jarren Duran|OF", "Triston Casas|1B", "Wilyer Abreu|OF", "Ceddanne Rafaela|OF", "Garrett Crochet|SP", "Tanner Houck|SP", "Aroldis Chapman|RP"],
    "NYY": ["Aaron Judge|OF", "Juan Soto|OF", "Anthony Volpe|SS", "Jazz Chisholm Jr.|2B", "Austin Wells|C", "Gerrit Cole|SP", "Carlos Rodon|SP", "Luke Weaver|RP"],
    "TB": ["Yandy Diaz|1B", "Brandon Lowe|2B", "Josh Lowe|OF", "Junior Caminero|3B", "Jonny DeLuca|OF", "Shane McClanahan|SP", "Ryan Pepiot|SP", "Pete Fairbanks|RP"],
    "TOR": ["Vladimir Guerrero Jr.|1B", "Bo Bichette|SS", "Daulton Varsho|OF", "Alejandro Kirk|C", "George Springer|OF", "Kevin Gausman|SP", "Jose Berrios|SP", "Jordan Romano|RP"],
    "CWS": ["Luis Robert Jr.|OF", "Andrew Vaughn|1B", "Korey Lee|C", "Miguel Vargas|3B", "Andrew Benintendi|OF", "Garrett Crochet|SP", "Jonathan Cannon|SP", "Michael Kopech|RP"],
    "CLE": ["Jose Ramirez|3B", "Steven Kwan|OF", "Josh Naylor|1B", "Andres Gimenez|2B", "Bo Naylor|C", "Tanner Bibee|SP", "Emmanuel Clase|RP", "Cade Smith|RP"],
    "DET": ["Riley Greene|OF", "Spencer Torkelson|1B", "Kerry Carpenter|OF", "Colt Keith|2B", "Dillon Dingler|C", "Tarik Skubal|SP", "Jack Flaherty|SP", "Jason Foley|RP"],
    "KC": ["Bobby Witt Jr.|SS", "Vinnie Pasquantino|1B", "Salvador Perez|C", "Maikel Garcia|3B", "MJ Melendez|OF", "Cole Ragans|SP", "Seth Lugo|SP", "Lucas Erceg|RP"],
    "MIN": ["Byron Buxton|OF", "Carlos Correa|SS", "Royce Lewis|3B", "Ryan Jeffers|C", "Matt Wallner|OF", "Pablo Lopez|SP", "Joe Ryan|SP", "Jhoan Duran|RP"],
    "ATH": ["Brent Rooker|OF", "Lawrence Butler|OF", "Shea Langeliers|C", "Tyler Soderstrom|1B", "Zack Gelof|2B", "JP Sears|SP", "Mason Miller|RP", "Joey Estes|SP"],
    "HOU": ["Yordan Alvarez|OF", "Jose Altuve|2B", "Alex Bregman|3B", "Kyle Tucker|OF", "Jeremy Pena|SS", "Framber Valdez|SP", "Hunter Brown|SP", "Josh Hader|RP"],
    "LAA": ["Mike Trout|OF", "Taylor Ward|OF", "Zach Neto|SS", "Logan O'Hoppe|C", "Nolan Schanuel|1B", "Reid Detmers|SP", "Jose Soriano|SP", "Ben Joyce|RP"],
    "SEA": ["Julio Rodriguez|OF", "Cal Raleigh|C", "J.P. Crawford|SS", "Randy Arozarena|OF", "Luke Raley|1B", "Logan Gilbert|SP", "George Kirby|SP", "Andres Munoz|RP"],
    "TEX": ["Corey Seager|SS", "Marcus Semien|2B", "Adolis Garcia|OF", "Wyatt Langford|OF", "Josh Jung|3B", "Nathan Eovaldi|SP", "Jacob deGrom|SP", "Kirby Yates|RP"],
    "ATL": ["Ronald Acuna Jr.|OF", "Matt Olson|1B", "Austin Riley|3B", "Ozzie Albies|2B", "Sean Murphy|C", "Chris Sale|SP", "Spencer Strider|SP", "Raisel Iglesias|RP"],
    "MIA": ["Jazz Chisholm|OF", "Jesus Sanchez|OF", "Otto Lopez|2B", "Xavier Edwards|SS", "Nick Fortes|C", "Sandy Alcantara|SP", "Edward Cabrera|SP", "Calvin Faucher|RP"],
    "NYM": ["Francisco Lindor|SS", "Pete Alonso|1B", "Brandon Nimmo|OF", "Mark Vientos|3B", "Francisco Alvarez|C", "Kodai Senga|SP", "Sean Manaea|SP", "Edwin Diaz|RP"],
    "PHI": ["Bryce Harper|1B", "Trea Turner|SS", "Kyle Schwarber|OF", "Alec Bohm|3B", "J.T. Realmuto|C", "Zack Wheeler|SP", "Aaron Nola|SP", "Jose Alvarado|RP"],
    "WSH": ["CJ Abrams|SS", "James Wood|OF", "Dylan Crews|OF", "Luis Garcia Jr.|2B", "Keibert Ruiz|C", "MacKenzie Gore|SP", "Jake Irvin|SP", "Kyle Finnegan|RP"],
    "CHC": ["Seiya Suzuki|OF", "Ian Happ|OF", "Dansby Swanson|SS", "Nico Hoerner|2B", "Michael Busch|1B", "Justin Steele|SP", "Shota Imanaga|SP", "Porter Hodge|RP"],
    "CIN": ["Elly De La Cruz|SS", "Spencer Steer|1B", "Tyler Stephenson|C", "TJ Friedl|OF", "Matt McLain|2B", "Hunter Greene|SP", "Nick Lodolo|SP", "Alexis Diaz|RP"],
    "MIL": ["Christian Yelich|OF", "William Contreras|C", "Jackson Chourio|OF", "Willy Adames|SS", "Rhys Hoskins|1B", "Freddy Peralta|SP", "Devin Williams|RP", "Trevor Megill|RP"],
    "PIT": ["Oneil Cruz|SS", "Bryan Reynolds|OF", "Ke'Bryan Hayes|3B", "Jared Triolo|2B", "Joey Bart|C", "Paul Skenes|SP", "Jared Jones|SP", "David Bednar|RP"],
    "STL": ["Nolan Arenado|3B", "Paul Goldschmidt|1B", "Masyn Winn|SS", "Lars Nootbaar|OF", "Willson Contreras|C", "Sonny Gray|SP", "Miles Mikolas|SP", "Ryan Helsley|RP"],
    "ARI": ["Ketel Marte|2B", "Corbin Carroll|OF", "Christian Walker|1B", "Eugenio Suarez|3B", "Gabriel Moreno|C", "Zac Gallen|SP", "Merrill Kelly|SP", "Paul Sewald|RP"],
    "COL": ["Ezequiel Tovar|SS", "Brenton Doyle|OF", "Ryan McMahon|3B", "Michael Toglia|1B", "Elias Diaz|C", "Kyle Freeland|SP", "Cal Quantrill|SP", "Tyler Kinley|RP"],
    "LAD": ["Shohei Ohtani|DH", "Mookie Betts|SS", "Freddie Freeman|1B", "Will Smith|C", "Teoscar Hernandez|OF", "Tyler Glasnow|SP", "Yoshinobu Yamamoto|SP", "Evan Phillips|RP"],
    "SD": ["Fernando Tatis Jr.|OF", "Manny Machado|3B", "Xander Bogaerts|SS", "Jackson Merrill|OF", "Luis Arraez|2B", "Dylan Cease|SP", "Michael King|SP", "Robert Suarez|RP"],
    "SF": ["Matt Chapman|3B", "Heliot Ramos|OF", "Jung Hoo Lee|OF", "Patrick Bailey|C", "LaMonte Wade Jr.|1B", "Logan Webb|SP", "Robbie Ray|SP", "Camilo Doval|RP"],
}

PITCHER_POS = {"SP", "RP"}


def _synth(role: str, n: int) -> pd.DataFrame:
    """Random component values with a mild latent 'true dawg' factor.

    The latent factor makes components correlate a little, so the resulting
    leaderboard has believable structure instead of pure noise.
    """
    latent = RNG.normal(0, 1, n)
    cols: dict[str, np.ndarray] = {}
    for pillar, comps in COMPONENTS[role].items():
        load = RNG.uniform(0.35, 0.7)
        for c, cfg in comps.items():
            cols[c] = latent * load + RNG.normal(0, 1, n) * (1 - load)
            k = cfg["k"]
            cols[f"{c}__n"] = RNG.uniform(k * 0.6, k * 4.0, n)
    return pd.DataFrame(cols)


def main() -> None:
    rows = {"hitter": [], "pitcher": []}
    pid = 100000
    for team, players in ROSTER.items():
        for entry in players:
            name, pos = entry.split("|")
            role = "pitcher" if pos in PITCHER_POS else "hitter"
            pid += 1
            rows[role].append(
                {"player_id": pid, "name": name, "team": team, "pos": pos}
            )

    scored = {}
    for role in ("hitter", "pitcher"):
        base = pd.DataFrame(rows[role])
        comps = _synth(role, len(base))
        df = pd.concat([base, comps], axis=1)
        df["opportunities"] = (
            RNG.uniform(280, 700, len(df)) if role == "hitter"
            else RNG.uniform(180, 800, len(df))
        )
        scored[role] = compute(df, role)

    payload = build_payload(
        scored["hitter"], scored["pitcher"], season=2026, synthetic=True
    )
    site = Path(__file__).resolve().parents[1] / "site"
    out = write_site_data(payload, site)

    print(f"wrote {out}  ({len(payload['players'])} players)")
    top = payload["players"][:5]
    for p in top:
        print(f"  {p['rank']:>2}. {p['name']:<24} {p['team']:<4} {p['dawg_plus']:.1f}")


if __name__ == "__main__":
    main()
