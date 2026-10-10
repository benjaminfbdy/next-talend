#!/usr/bin/env python3
"""Génère le jeu de données FICTIF du POC « mini-chaîne de conformité assurance non-vie ».

Sorties (dossier --out, par défaut data/generated/) :
  clients.csv, contrats.csv, sinistres.csv, garanties.csv   fichiers sources (UTF-8, séparateur « ; »)
  expected_rejects.csv   oracle : (fichier, rang_ligne, motif) attendus en table de rejets
  expected_results.json  oracle : volumes attendus (rejets, acceptés, purge) + règles + empreintes

Principes
  * Données 100 % fictives : noms tirés de listes génériques, e-mails en domaines réservés
    (RFC 2606 : example.com / .org / .net / .example), aucune donnée réelle.
  * Défauts volontaires, chacun injecté sur une ligne qui n'en porte qu'UN seul : les volumes de
    rejets attendus sont donc exacts et vérifiables (tests fonctionnels du job Talend).
  * Aucune ligne « saine » ne dépend d'une ligne rejetée (pas de contrat rattaché à un client
    rejeté) : pas de rejet en cascade, la politique de cascade reste un choix de conception à part.
  * Reproductible : même --seed et même --ref-date => mêmes fichiers (sur une même version de Python).
  * La date de référence est un PARAMÈTRE (jamais « maintenant ») : rejouabilité et tests
    déterministes. Dans Talend elle deviendra la variable de contexte `ref_date`.
  * Auto-contrôle : une implémentation indépendante des règles (fonctions check_*) rejoue les
    fichiers écrits et doit retrouver exactement les défauts injectés, sinon le script échoue.

Usage : python data/generate_dataset.py [--seed 42] [--ref-date 2026-10-01] [--out data/generated]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import sys
import unicodedata
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

# --------------------------------------------------------------------------------------------
# Volumes et défauts injectés (constantes : ce sont elles qui fixent les volumes attendus)
# --------------------------------------------------------------------------------------------
N_CLIENTS, N_CONTRATS, N_SINISTRES = 3000, 4500, 2500
N_BORNES = 6  # contrats « frontière » de la purge : N_BORNES pile à 2 ans (conservés), N_BORNES à 2 ans + 1 jour (purgés)

CLIENT_DEFECTS = {"nom_vide": 30, "prenom_vide": 20, "naissance_vide": 20, "naissance_invalide": 40, "email_invalide": 90}
CLIENT_DOUBLONS, CLIENT_DOUBLONS_MAJ, CLIENT_DOUBLONS_ESPACES = 60, 10, 10  # le reste : copies strictes
CLIENT_ESPACES_VALIDES = 100  # lignes VALIDES avec espaces parasites : ne doivent PAS être rejetées

CONTRAT_DEFECTS = {"client_vide": 15, "produit_vide": 10, "effet_vide": 15, "statut_vide": 10,
                   "orphelin_client": 40, "dates_incoherentes": 30, "fin_absente": 20}
CONTRAT_DOUBLONS = 40

SINISTRE_DEFECTS = {"survenance_vide": 15, "garantie_vide": 10, "contrat_vide": 10, "orphelin_contrat": 30,
                    "garantie_inconnue": 25, "dates_incoherentes": 25, "hors_periode": 25, "montant_negatif": 20}
SINISTRE_DOUBLONS = 30

# --------------------------------------------------------------------------------------------
# Référentiels
# --------------------------------------------------------------------------------------------
GARANTIES = [  # code, libellé, famille
    ("RC_AUTO", "Responsabilité civile automobile", "RESPONSABILITE_CIVILE"),
    ("DOMM_COLL", "Dommages collision", "DOMMAGES"),
    ("VOL_VEH", "Vol du véhicule", "VOL"),
    ("BRIS_GLACE", "Bris de glace", "DOMMAGES"),
    ("INCENDIE_VEH", "Incendie du véhicule", "DOMMAGES"),
    ("ASSISTANCE", "Assistance dépannage", "ASSISTANCE"),
    ("DEGATS_EAUX", "Dégâts des eaux", "DOMMAGES"),
    ("INCENDIE_HAB", "Incendie de l'habitation", "DOMMAGES"),
    ("VOL_HAB", "Vol et vandalisme habitation", "VOL"),
    ("CATNAT", "Catastrophes naturelles", "CATASTROPHES_NATURELLES"),
    ("RC_VIE_PRIV", "Responsabilité civile vie privée", "RESPONSABILITE_CIVILE"),
    ("ACCIDENT_VIE", "Garantie des accidents de la vie", "PREVOYANCE"),
]
GARANTIES_PAR_PRODUIT = {
    "AUTO": ["RC_AUTO", "DOMM_COLL", "VOL_VEH", "BRIS_GLACE", "INCENDIE_VEH", "ASSISTANCE"],
    "MOTO": ["RC_AUTO", "VOL_VEH", "DOMM_COLL"],
    "HABITATION": ["DEGATS_EAUX", "INCENDIE_HAB", "VOL_HAB", "CATNAT", "RC_VIE_PRIV"],
    "GAV": ["ACCIDENT_VIE"],
}
PRODUITS = ["AUTO", "HABITATION", "MOTO", "GAV"]
PRODUITS_POIDS = [40, 35, 10, 15]
PRIME_ANNUELLE = {"AUTO": (350, 1100), "MOTO": (180, 700), "HABITATION": (120, 520), "GAV": (80, 260)}
MONTANT_MEDIAN = {"RC_AUTO": 2500, "DOMM_COLL": 1800, "VOL_VEH": 6500, "BRIS_GLACE": 350, "INCENDIE_VEH": 5200,
                  "ASSISTANCE": 180, "DEGATS_EAUX": 1400, "INCENDIE_HAB": 9500, "VOL_HAB": 2800, "CATNAT": 4800,
                  "RC_VIE_PRIV": 1600, "ACCIDENT_VIE": 7000}
GARANTIES_FANTAISISTES = ["GAR99", "XXX", "RC_MOTO", "ZZ_TEST", "DEG_EAU", "VOL"]

PRENOMS_M = ["Jean", "Pierre", "Michel", "Philippe", "Alain", "Patrick", "Nicolas", "Christophe", "Laurent", "Éric",
             "Thomas", "Julien", "David", "Sébastien", "Stéphane", "Olivier", "Frédéric", "Antoine", "Maxime", "Hugo",
             "Lucas", "Louis", "Paul", "Mathieu", "Romain", "Kévin", "Yann", "Karim", "Mehdi", "Youssef", "Jean-Pierre"]
PRENOMS_F = ["Marie", "Nathalie", "Isabelle", "Sylvie", "Catherine", "Françoise", "Martine", "Christine", "Sophie",
             "Céline", "Julie", "Camille", "Léa", "Emma", "Chloé", "Manon", "Sarah", "Laura", "Aurélie", "Émilie",
             "Claire", "Anne", "Élodie", "Amina", "Fatima", "Inès", "Marie-Claire"]
NOMS = ["Martin", "Bernard", "Dubois", "Thomas", "Robert", "Richard", "Petit", "Durand", "Leroy", "Moreau", "Simon",
        "Laurent", "Lefebvre", "Michel", "Garcia", "David", "Bertrand", "Roux", "Vincent", "Fournier", "Morel", "Girard",
        "André", "Mercier", "Dupont", "Lambert", "Bonnet", "François", "Martinez", "Legrand", "Garnier", "Faure",
        "Rousseau", "Blanc", "Guérin", "Muller", "Henry", "Roussel", "Nicolas", "Perrin", "Morin", "Mathieu", "Clément",
        "Gauthier", "Dumont", "Lopez", "Fontaine", "Chevalier", "Robin", "Masson", "Sanchez", "Gérard", "Nguyen",
        "Boyer", "Denis", "Lemaire", "Duval", "Joly", "Benoît", "Ben Salah", "Haddad", "Da Silva", "Ferreira"]
VILLES = [("Paris", "75011"), ("Lyon", "69003"), ("Marseille", "13008"), ("Toulouse", "31000"), ("Nice", "06000"),
          ("Nantes", "44000"), ("Montpellier", "34000"), ("Strasbourg", "67000"), ("Bordeaux", "33000"),
          ("Lille", "59000"), ("Rennes", "35000"), ("Reims", "51100"), ("Saint-Étienne", "42000"),
          ("Le Havre", "76600"), ("Toulon", "83000"), ("Grenoble", "38000"), ("Dijon", "21000"), ("Angers", "49000"),
          ("Nîmes", "30000"), ("Villeurbanne", "69100"), ("Clermont-Ferrand", "63000"), ("Le Mans", "72000"),
          ("Aix-en-Provence", "13100"), ("Brest", "29200"), ("Tours", "37000"), ("Limoges", "87000"),
          ("Amiens", "80000"), ("Perpignan", "66000"), ("Metz", "57000"), ("Besançon", "25000"), ("Orléans", "45000"),
          ("Rouen", "76000"), ("Mulhouse", "68100"), ("Caen", "14000"), ("Nancy", "54000"), ("Pau", "64000"),
          ("La Rochelle", "17000"), ("Poitiers", "86000"), ("Ajaccio", "20000"), ("Chambéry", "73000")]
DOMAINES = ["example.com", "example.org", "example.net", "mail.example", "courriel.example"]  # domaines réservés (RFC 2606)

CLIENT_COLS = ["client_id", "civilite", "nom", "prenom", "date_naissance", "email", "code_postal", "ville", "date_creation"]
CONTRAT_COLS = ["contrat_id", "client_id", "produit", "date_effet", "date_fin", "statut", "prime_annuelle"]
SINISTRE_COLS = ["sinistre_id", "contrat_id", "garantie_code", "date_survenance", "date_declaration", "statut",
                 "montant_estime", "montant_regle"]
GARANTIE_COLS = ["garantie_code", "libelle", "famille"]

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

REGLES = {
    "normalisation": "trim de toutes les colonnes avant tout contrôle ; une valeur blanche équivaut à vide",
    "ordre_des_controles": "obligatoires > formats/cohérence > référentiel > période/montant > doublon (le 1er motif en échec gagne)",
    "clients": ["client_id, nom, prenom, date_naissance obligatoires",
                "date_naissance : ISO yyyy-MM-dd valide, comprise entre ref_date-120 ans et ref_date",
                "email facultatif ; s'il est renseigné il doit respecter " + EMAIL_RE.pattern,
                "doublon : même client_id déjà accepté plus haut dans le fichier (1re occurrence conservée)"],
    "contrats": ["contrat_id, client_id, produit, date_effet, statut obligatoires",
                 "client_id doit exister parmi les clients ACCEPTÉS",
                 "date_fin >= date_effet", "statut <> ACTIF => date_fin obligatoire",
                 "doublon sur contrat_id (1re occurrence conservée)"],
    "sinistres": ["sinistre_id, contrat_id, garantie_code, date_survenance, date_declaration obligatoires",
                  "contrat_id doit exister parmi les contrats ACCEPTÉS", "garantie_code doit exister dans garanties.csv",
                  "date_declaration >= date_survenance",
                  "date_survenance dans [date_effet ; date_fin, ou ref_date si contrat actif] (sinon hors période de garantie)",
                  "montant_regle >= 0 s'il est renseigné", "doublon sur sinistre_id (1re occurrence conservée)"],
    "purge": "contrat accepté, statut <> ACTIF, date_fin < ref_date - 2 ans (strict : pile 2 ans = conservé) ; ses sinistres suivent",
}


# --------------------------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------------------------
def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 février
        return d.replace(year=d.year + years, day=28)


def rand_date(rng: random.Random, lo: date, hi: date) -> date:
    return lo + timedelta(days=rng.randint(0, (hi - lo).days))


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def parse_iso(value: str) -> date | None:
    if not ISO_RE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def money(x: float) -> str:
    return f"{x:.2f}"


def tag(row: dict, motif: str, champ: str, variante: str = "") -> None:
    row["_tag"] = {"motif": motif, "champ": champ, "variante": variante}


def blank(rng: random.Random, row: dict, field: str) -> None:
    row[field] = rng.choice(["", "", "   "])  # vide ou blanc : les deux doivent être traités comme « vide »


def take(pool: list, n: int) -> list:
    chunk = pool[:n]
    del pool[:n]
    return chunk


def insert_duplicates(rng: random.Random, rows: list[dict], sources: list[dict], variants: list[str], key: str) -> None:
    """Insère une copie de chaque source APRÈS l'original (la 1re occurrence reste la bonne)."""
    for src, variant in zip(sources, variants):
        copy = dict(src)
        if variant == "maj":
            copy["nom"], copy["email"] = copy["nom"].upper(), copy["email"].upper()
        elif variant == "espaces":
            for k in ("nom", "prenom", "email"):
                copy[k] = f"  {copy[k]} "
        tag(copy, "DOUBLON", key, variant or "copie_stricte")
        pos = next(i for i, r in enumerate(rows) if r is src)
        rows.insert(rng.randint(pos + 1, len(rows)), copy)


# --------------------------------------------------------------------------------------------
# Construction des données saines
# --------------------------------------------------------------------------------------------
def build_clients(rng: random.Random, ref: date) -> list[dict]:
    rows = []
    for i in range(1, N_CLIENTS + 1):
        feminin = rng.random() < 0.5
        prenom = rng.choice(PRENOMS_F if feminin else PRENOMS_M)
        nom = rng.choice(NOMS)
        naissance = ref - timedelta(days=rng.randint(20 * 365, 95 * 365))
        creation = rand_date(rng, max(date(2012, 1, 1), add_years(naissance, 18)), ref - timedelta(days=30))
        ville, cp = rng.choice(VILLES)
        email_ok = f"{slug(prenom)}.{slug(nom)}{rng.randint(1, 99) if rng.random() < 0.5 else ''}@{rng.choice(DOMAINES)}"
        rows.append({
            "client_id": f"CLI{i:06d}", "civilite": "MME" if feminin else "M", "nom": nom, "prenom": prenom,
            "date_naissance": naissance.isoformat(),
            "email": email_ok if rng.random() < 0.90 else "",  # 10 % sans e-mail : légitime (facultatif)
            "code_postal": cp, "ville": ville, "date_creation": creation.isoformat(),
            "_email_ok": email_ok, "_tag": None,
        })
    return rows


def bad_email(rng: random.Random, good: str) -> tuple[str, str]:
    local, _, dom = good.partition("@")
    variants = {
        "sans_arobase": f"{local}{dom}", "sans_domaine": f"{local}@", "sans_local": f"@{dom}",
        "espace": f"{local.replace('.', ' ')}@{dom}", "double_arobase": f"{local}@@{dom}",
        "sans_tld": f"{local}@{dom.split('.')[0]}", "points_consecutifs": f"{local}@{dom.replace('.', '..', 1)}",
        "texte_libre": rng.choice(["N/A", "non renseigné", "-", "inconnu", "NULL"]),
    }
    name = rng.choice(list(variants))
    return variants[name], name


def inject_clients(rng: random.Random, ref: date, rows: list[dict]) -> list[dict]:
    pool = list(rows)
    rng.shuffle(pool)
    for r in take(pool, CLIENT_DEFECTS["nom_vide"]):
        blank(rng, r, "nom"); tag(r, "CHAMP_OBLIGATOIRE_VIDE", "nom")
    for r in take(pool, CLIENT_DEFECTS["prenom_vide"]):
        blank(rng, r, "prenom"); tag(r, "CHAMP_OBLIGATOIRE_VIDE", "prenom")
    for r in take(pool, CLIENT_DEFECTS["naissance_vide"]):
        blank(rng, r, "date_naissance"); tag(r, "CHAMP_OBLIGATOIRE_VIDE", "date_naissance")
    for r in take(pool, CLIENT_DEFECTS["naissance_invalide"]):
        nd = date.fromisoformat(r["date_naissance"])
        variante = rng.choice(["jour_impossible", "mois_13", "compact", "format_fr", "futur", "trop_ancien"])
        r["date_naissance"] = {
            "jour_impossible": f"{nd.year}-02-30", "mois_13": f"{nd.year}-13-{nd.day:02d}",
            "compact": nd.strftime("%Y%m%d"), "format_fr": nd.strftime("%d/%m/%Y"),
            "futur": (ref + timedelta(days=rng.randint(30, 3000))).isoformat(),
            "trop_ancien": f"{rng.randint(1850, 1890)}-{nd.month:02d}-{min(nd.day, 28):02d}",
        }[variante]
        tag(r, "DATE_INVALIDE", "date_naissance", variante)
    for r in take(pool, CLIENT_DEFECTS["email_invalide"]):
        r["email"], variante = bad_email(rng, r["_email_ok"])
        tag(r, "EMAIL_INVALIDE", "email", variante)
    saines = [r for r in rows if r["_tag"] is None]
    for r in rng.sample(saines, CLIENT_ESPACES_VALIDES):  # valides mais « sales » : à nettoyer, pas à rejeter
        r["nom"], r["prenom"] = f"  {r['nom']}", f"{r['prenom']} "
    sources = rng.sample(saines, CLIENT_DOUBLONS)
    variants = (["maj"] * CLIENT_DOUBLONS_MAJ + ["espaces"] * CLIENT_DOUBLONS_ESPACES
                + [""] * (CLIENT_DOUBLONS - CLIENT_DOUBLONS_MAJ - CLIENT_DOUBLONS_ESPACES))
    insert_duplicates(rng, rows, sources, variants, "client_id")
    return rows


def build_contrats(rng: random.Random, ref: date, clients_ok: list[dict]) -> list[dict]:
    seuil = add_years(ref, -2)
    kinds = ["BORNE_PILE"] * N_BORNES + ["BORNE_PLUS1"] * N_BORNES + ["NORMAL"] * (N_CONTRATS - 2 * N_BORNES)
    rng.shuffle(kinds)  # les contrats frontière ne sont pas regroupés en tête de fichier
    rows = []
    for i, kind in enumerate(kinds, 1):
        produit = rng.choices(PRODUITS, PRODUITS_POIDS)[0]
        if kind != "NORMAL":
            fin = seuil if kind == "BORNE_PILE" else seuil - timedelta(days=1)
            effet, statut = fin - timedelta(days=rng.randint(400, 2500)), rng.choice(["ECHU", "RESILIE"])
        elif rng.random() < 0.38:
            effet = rand_date(rng, date(2014, 1, 1), ref - timedelta(days=400))
            fin = rand_date(rng, effet + timedelta(days=365), ref - timedelta(days=1))
            statut = rng.choices(["RESILIE", "ECHU"], [60, 40])[0]
        else:
            effet, fin, statut = rand_date(rng, date(2014, 1, 1), ref - timedelta(days=60)), None, "ACTIF"
        lo, hi = PRIME_ANNUELLE[produit]
        rows.append({
            "contrat_id": f"CTR{i:06d}", "client_id": rng.choice(clients_ok)["client_id"], "produit": produit,
            "date_effet": effet.isoformat(), "date_fin": fin.isoformat() if fin else "", "statut": statut,
            "prime_annuelle": money(rng.uniform(lo, hi)), "_borne": kind != "NORMAL", "_tag": None,
        })
    return rows


def inject_contrats(rng: random.Random, rows: list[dict]) -> list[dict]:
    pool = [r for r in rows if not r["_borne"]]
    rng.shuffle(pool)
    for key, field in (("client_vide", "client_id"), ("produit_vide", "produit"), ("effet_vide", "date_effet"),
                       ("statut_vide", "statut")):
        for r in take(pool, CONTRAT_DEFECTS[key]):
            blank(rng, r, field); tag(r, "CHAMP_OBLIGATOIRE_VIDE", field)
    for n, r in enumerate(take(pool, CONTRAT_DEFECTS["orphelin_client"]), 1):
        r["client_id"] = f"CLI9{n:05d}"; tag(r, "REF_CLIENT_INCONNU", "client_id")
    for r in take(pool, CONTRAT_DEFECTS["dates_incoherentes"]):
        r["statut"] = "RESILIE"
        r["date_fin"] = (date.fromisoformat(r["date_effet"]) - timedelta(days=rng.randint(1, 300))).isoformat()
        tag(r, "DATES_INCOHERENTES", "date_fin")
    for r in take(pool, CONTRAT_DEFECTS["fin_absente"]):
        r["statut"], r["date_fin"] = rng.choice(["RESILIE", "ECHU"]), ""
        tag(r, "DATE_FIN_ABSENTE", "date_fin")
    saines = [r for r in rows if r["_tag"] is None and not r["_borne"]]
    insert_duplicates(rng, rows, rng.sample(saines, CONTRAT_DOUBLONS), [""] * CONTRAT_DOUBLONS, "contrat_id")
    return rows


def build_sinistres(rng: random.Random, ref: date, contrats_ok: list[dict]) -> list[dict]:
    rows = []
    for i in range(1, N_SINISTRES + 1):
        while True:
            c = rng.choice(contrats_ok)
            debut = date.fromisoformat(c["date_effet"])
            fin = date.fromisoformat(c["date_fin"]) if c["date_fin"] else ref
            fin = min(fin, ref - timedelta(days=5))
            if debut < fin:
                break
        survenance = rand_date(rng, debut, fin)
        declaration = min(survenance + timedelta(days=rng.randint(0, 45)), ref)
        garantie = rng.choice(GARANTIES_PAR_PRODUIT[c["produit"]])
        estime = max(30.0, rng.lognormvariate(math.log(MONTANT_MEDIAN[garantie]), 0.7))
        age = (ref - survenance).days
        tirage = rng.random()
        if age < 60 and tirage < 0.60:
            statut, regle = "OUVERT", ""
        elif age >= 60 and tirage < 0.03:
            statut, regle = "OUVERT", ""
        elif age >= 60 and tirage < 0.08:
            statut, regle = "SANS_SUITE", money(0)
        else:
            statut, regle = "CLOS", money(estime * rng.uniform(0.55, 1.0))
        rows.append({
            "sinistre_id": f"SIN{i:06d}", "contrat_id": c["contrat_id"], "garantie_code": garantie,
            "date_survenance": survenance.isoformat(), "date_declaration": declaration.isoformat(),
            "statut": statut, "montant_estime": money(estime), "montant_regle": regle, "_tag": None,
        })
    return rows


def inject_sinistres(rng: random.Random, ref: date, rows: list[dict], contrats: list[dict]) -> list[dict]:
    par_id = {c["contrat_id"]: c for c in contrats}
    pool = list(rows)
    rng.shuffle(pool)
    for key, field in (("survenance_vide", "date_survenance"), ("garantie_vide", "garantie_code"),
                       ("contrat_vide", "contrat_id")):
        for r in take(pool, SINISTRE_DEFECTS[key]):
            blank(rng, r, field); tag(r, "CHAMP_OBLIGATOIRE_VIDE", field)
    for n, r in enumerate(take(pool, SINISTRE_DEFECTS["orphelin_contrat"]), 1):
        r["contrat_id"] = f"CTR9{n:05d}"; tag(r, "REF_CONTRAT_INCONNU", "contrat_id")
    for r in take(pool, SINISTRE_DEFECTS["garantie_inconnue"]):
        r["garantie_code"] = rng.choice(GARANTIES_FANTAISISTES); tag(r, "REF_GARANTIE_INCONNUE", "garantie_code")
    for r in take(pool, SINISTRE_DEFECTS["dates_incoherentes"]):
        r["date_declaration"] = (date.fromisoformat(r["date_survenance"]) - timedelta(days=rng.randint(1, 60))).isoformat()
        tag(r, "DATES_INCOHERENTES", "date_declaration")
    for r in take(pool, SINISTRE_DEFECTS["hors_periode"]):
        c = par_id[r["contrat_id"]]
        debut = date.fromisoformat(c["date_effet"])
        fin = date.fromisoformat(c["date_fin"]) if c["date_fin"] else None
        if fin and (ref - timedelta(days=1) - fin).days >= 30 and rng.random() < 0.5:
            surv = fin + timedelta(days=rng.randint(30, min(400, (ref - timedelta(days=1) - fin).days)))
            variante = "apres_fin"
        else:
            surv, variante = debut - timedelta(days=rng.randint(30, 400)), "avant_effet"
        r["date_survenance"] = surv.isoformat()
        r["date_declaration"] = min(surv + timedelta(days=rng.randint(0, 30)), ref).isoformat()
        tag(r, "HORS_PERIODE_GARANTIE", "date_survenance", variante)
    for r in take(pool, SINISTRE_DEFECTS["montant_negatif"]):
        r["statut"], r["montant_regle"] = "CLOS", money(-rng.uniform(50, 3000))
        tag(r, "MONTANT_NEGATIF", "montant_regle")
    saines = [r for r in rows if r["_tag"] is None]
    insert_duplicates(rng, rows, rng.sample(saines, SINISTRE_DOUBLONS), [""] * SINISTRE_DOUBLONS, "sinistre_id")
    return rows


# --------------------------------------------------------------------------------------------
# Oracle : implémentation INDÉPENDANTE des règles, rejouée sur les lignes écrites
# --------------------------------------------------------------------------------------------
def _norm(row: dict, cols: list[str]) -> dict:
    return {k: (row.get(k) or "").strip() for k in cols}


def _vides(r: dict, cols: tuple[str, ...]) -> str | None:
    return next((c for c in cols if not r[c]), None)


def check_clients(rows: list[dict], ref: date):
    rejets, acceptes, vus = {}, [], set()
    plancher = add_years(ref, -120)
    for rang, row in enumerate(rows, 1):
        r = _norm(row, CLIENT_COLS)
        motif = None
        if (champ := _vides(r, ("client_id", "nom", "prenom", "date_naissance"))):
            motif = ("CHAMP_OBLIGATOIRE_VIDE", champ)
        elif (d := parse_iso(r["date_naissance"])) is None or d > ref or d < plancher:
            motif = ("DATE_INVALIDE", "date_naissance")
        elif r["email"] and not EMAIL_RE.match(r["email"]):
            motif = ("EMAIL_INVALIDE", "email")
        elif r["client_id"] in vus:
            motif = ("DOUBLON", "client_id")
        if motif:
            rejets[rang] = motif
        else:
            vus.add(r["client_id"]); acceptes.append(r)
    return rejets, acceptes


def check_contrats(rows: list[dict], clients_ok: set[str]):
    rejets, acceptes, vus = {}, [], set()
    for rang, row in enumerate(rows, 1):
        r = _norm(row, CONTRAT_COLS)
        motif = None
        if (champ := _vides(r, ("contrat_id", "client_id", "produit", "date_effet", "statut"))):
            motif = ("CHAMP_OBLIGATOIRE_VIDE", champ)
        else:
            effet, fin = parse_iso(r["date_effet"]), parse_iso(r["date_fin"]) if r["date_fin"] else None
            if r["client_id"] not in clients_ok:
                motif = ("REF_CLIENT_INCONNU", "client_id")
            elif fin and fin < effet:
                motif = ("DATES_INCOHERENTES", "date_fin")
            elif r["statut"] != "ACTIF" and not fin:
                motif = ("DATE_FIN_ABSENTE", "date_fin")
            elif r["contrat_id"] in vus:
                motif = ("DOUBLON", "contrat_id")
        if motif:
            rejets[rang] = motif
        else:
            vus.add(r["contrat_id"]); acceptes.append(r)
    return rejets, acceptes


def check_sinistres(rows: list[dict], ref: date, contrats_ok: dict[str, dict], garanties: set[str]):
    rejets, acceptes, vus = {}, [], set()
    for rang, row in enumerate(rows, 1):
        r = _norm(row, SINISTRE_COLS)
        motif = None
        if (champ := _vides(r, ("sinistre_id", "contrat_id", "garantie_code", "date_survenance", "date_declaration"))):
            motif = ("CHAMP_OBLIGATOIRE_VIDE", champ)
        else:
            surv, decl = parse_iso(r["date_survenance"]), parse_iso(r["date_declaration"])
            c = contrats_ok.get(r["contrat_id"])
            if c is None:
                motif = ("REF_CONTRAT_INCONNU", "contrat_id")
            elif r["garantie_code"] not in garanties:
                motif = ("REF_GARANTIE_INCONNUE", "garantie_code")
            elif decl < surv:
                motif = ("DATES_INCOHERENTES", "date_declaration")
            elif not (parse_iso(c["date_effet"]) <= surv <= (parse_iso(c["date_fin"]) if c["date_fin"] else ref)):
                motif = ("HORS_PERIODE_GARANTIE", "date_survenance")
            elif r["montant_regle"] and float(r["montant_regle"]) < 0:
                motif = ("MONTANT_NEGATIF", "montant_regle")
            elif r["sinistre_id"] in vus:
                motif = ("DOUBLON", "sinistre_id")
        if motif:
            rejets[rang] = motif
        else:
            vus.add(r["sinistre_id"]); acceptes.append(r)
    return rejets, acceptes


def verifier(fichier: str, rows: list[dict], rejets: dict[int, tuple[str, str]]) -> None:
    attendu = {rang: (r["_tag"]["motif"], r["_tag"]["champ"]) for rang, r in enumerate(rows, 1) if r["_tag"]}
    if attendu != rejets:
        manquants = {k: attendu[k] for k in attendu.keys() - rejets.keys()}
        en_trop = {k: rejets[k] for k in rejets.keys() - attendu.keys()}
        divergents = {k: (attendu[k], rejets[k]) for k in attendu.keys() & rejets.keys() if attendu[k] != rejets[k]}
        raise AssertionError(f"{fichier} : oracle incohérent — manquants={list(manquants.items())[:3]} "
                             f"en_trop={list(en_trop.items())[:3]} divergents={list(divergents.items())[:3]}")


# --------------------------------------------------------------------------------------------
# Écriture
# --------------------------------------------------------------------------------------------
def write_csv(path: Path, cols: list[str], rows: list) -> str:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter=";", extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--ref-date", default="2026-10-01", help="date de référence (yyyy-MM-dd), jamais « maintenant »")
    ap.add_argument("--out", default=str(Path(__file__).parent / "generated"))
    args = ap.parse_args()
    ref, out, rng = date.fromisoformat(args.ref_date), Path(args.out), random.Random(args.seed)
    out.mkdir(parents=True, exist_ok=True)

    clients = inject_clients(rng, ref, build_clients(rng, ref))
    clients_sains = [r for r in clients if r["_tag"] is None]  # seuls clients auxquels on rattache des contrats
    contrats = inject_contrats(rng, build_contrats(rng, ref, clients_sains))
    contrats_sains = [r for r in contrats if r["_tag"] is None]
    sinistres = inject_sinistres(rng, ref, build_sinistres(rng, ref, contrats_sains), contrats)
    garanties = [dict(zip(GARANTIE_COLS, g)) for g in GARANTIES]

    # Auto-contrôle : l'oracle indépendant doit retrouver exactement les défauts injectés.
    rej_cli, cli_ok = check_clients(clients, ref)
    verifier("clients.csv", clients, rej_cli)
    rej_ctr, ctr_ok = check_contrats(contrats, {c["client_id"] for c in cli_ok})
    verifier("contrats.csv", contrats, rej_ctr)
    ctr_ok_par_id = {c["contrat_id"]: c for c in ctr_ok}
    rej_sin, sin_ok = check_sinistres(sinistres, ref, ctr_ok_par_id, {g["garantie_code"] for g in garanties})
    verifier("sinistres.csv", sinistres, rej_sin)

    fichiers = {
        "clients.csv": (CLIENT_COLS, clients, rej_cli, cli_ok),
        "contrats.csv": (CONTRAT_COLS, contrats, rej_ctr, ctr_ok),
        "sinistres.csv": (SINISTRE_COLS, sinistres, rej_sin, sin_ok),
    }
    empreintes = {nom: write_csv(out / nom, cols, rows) for nom, (cols, rows, _, _) in fichiers.items()}
    empreintes["garanties.csv"] = write_csv(out / "garanties.csv", GARANTIE_COLS, garanties)

    cle = {"clients.csv": "client_id", "contrats.csv": "contrat_id", "sinistres.csv": "sinistre_id"}
    with (out / "expected_rejects.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";", lineterminator="\n")
        w.writerow(["fichier", "rang_ligne", "cle_metier", "motif", "champ", "variante"])
        for nom, (_, rows, rejets, _) in fichiers.items():
            for rang in sorted(rejets):
                t = rows[rang - 1]["_tag"]
                w.writerow([nom, rang, rows[rang - 1][cle[nom]].strip(), t["motif"], t["champ"], t["variante"]])

    seuil = add_years(ref, -2)
    a_purger = [c for c in ctr_ok if c["statut"] != "ACTIF" and parse_iso(c["date_fin"]) < seuil]
    ids_purge = {c["contrat_id"] for c in a_purger}
    manifest = {
        "genere_avec": {"script": "data/generate_dataset.py", "seed": args.seed, "date_reference": ref.isoformat(),
                        "python": sys.version.split()[0]},
        "format": {"encodage": "UTF-8 sans BOM", "separateur": ";", "dates": "yyyy-MM-dd", "decimal": ".",
                   "rang_ligne": "1 = première ligne de données (en-tête exclu), comme le compteur de tFileInputDelimited"},
        "regles": REGLES,
        "fichiers": {
            nom: {"lignes": len(rows), "acceptees": len(ok), "rejetees": len(rejets),
                  "rejets_par_motif": dict(sorted(Counter(m for m, _ in rejets.values()).items())),
                  "sha256": empreintes[nom]}
            for nom, (_, rows, rejets, ok) in fichiers.items()
        } | {"garanties.csv": {"lignes": len(garanties), "acceptees": len(garanties), "rejetees": 0,
                               "rejets_par_motif": {}, "sha256": empreintes["garanties.csv"]}},
        "valides_a_nettoyer": {"clients_avec_espaces_parasites": CLIENT_ESPACES_VALIDES,
                               "clients_sans_email_legitime": sum(1 for c in cli_ok if not c["email"])},
        "purge": {"seuil_strict": seuil.isoformat(), "contrats_a_purger": len(a_purger),
                  "sinistres_lies_a_purger": sum(1 for s in sin_ok if s["contrat_id"] in ids_purge),
                  "contrats_frontiere_conserves": sum(1 for c in ctr_ok if c["date_fin"] == seuil.isoformat()),
                  "contrats_frontiere_purges": sum(1 for c in a_purger if c["date_fin"] == (seuil - timedelta(days=1)).isoformat())},
        "totaux": {"lignes_sources": sum(len(r) for _, r, _, _ in fichiers.values()) + len(garanties)},
    }
    (out / "expected_results.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Jeu de données fictif généré dans {out}  (seed={args.seed}, date de référence={ref})")
    for nom, info in manifest["fichiers"].items():
        print(f"  {nom:15s} {info['lignes']:>6d} lignes  {info['acceptees']:>6d} acceptées  {info['rejetees']:>5d} rejetées")
        for motif, n in info["rejets_par_motif"].items():
            print(f"      {motif:26s} {n:>4d}")
    p = manifest["purge"]
    print(f"  Purge (date_fin < {p['seuil_strict']}) : {p['contrats_a_purger']} contrats, {p['sinistres_lies_a_purger']} sinistres liés")
    print(f"  Total : {manifest['totaux']['lignes_sources']} lignes sources — auto-contrôle de l'oracle : OK")


if __name__ == "__main__":
    main()
