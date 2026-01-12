import sys
import os
import asyncio
import re
import difflib
from datetime import datetime

# Ajout du path pour les imports
SCHOLAR_PATH = r"D:\Claude Code\scholar-mcp"
if SCHOLAR_PATH not in sys.path:
    sys.path.append(SCHOLAR_PATH)

from src.services import Orchestrator


def clean_latex(text):
    """Nettoie agressivement le texte pour ignorer les erreurs de caractères."""
    if not text:
        return ""
    # Enlève les commandes LaTeX (ex: \ac{...}, \textbf{...})
    text = re.sub(r"\\[\w]+(?:\{.*?\})?", "", text)
    # Enlève les accolades et la ponctuation
    text = re.sub(r"[^\w\s]", " ", text)
    # Remplace les tirets et underscores par des espaces
    text = text.replace("_", " ").replace("-", " ")
    # Met en minuscule et normalise les espaces
    return "".join(text.lower().split())


def calculate_similarity(s1, s2):
    """Compare uniquement les caractères alphanumériques."""
    c1 = clean_latex(s1)
    c2 = clean_latex(s2)
    if not c1 or not c2:
        return 0.0
    if c1 == c2:
        return 1.0
    return difflib.SequenceMatcher(None, c1, c2).ratio()


def parse_bib_file(file_path):
    entries = []
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    raw_blocks = content.split("@")
    for block in raw_blocks:
        if not block.strip():
            continue
        try:
            header_match = re.match(r"(\w+)\s*\{\s*([^,]+),", block)
            if not header_match:
                continue
            key = header_match.group(2).strip()
            title_match = re.search(
                r'title\s*=\s*[\{{"](.*?)[\}}"]', block, re.IGNORECASE | re.DOTALL
            )
            doi_match = re.search(r'doi\s*=\s*[\{{"](.*?)[\}}"]', block, re.IGNORECASE)
            year_match = re.search(
                r'year\s*=\s*[\{{"]?(\d{4})[\}}"]?', block, re.IGNORECASE
            )
            title = (
                title_match.group(1).replace("\n", " ").strip() if title_match else None
            )
            doi = doi_match.group(1).strip() if doi_match else None
            year = year_match.group(1).strip() if year_match else None
            if title:
                entries.append({"key": key, "title": title, "doi": doi, "year": year})
        except Exception:
            continue
    return entries


async def audit_entry(entry, orchestrator):
    result = {"key": entry["key"], "status": "OK", "issues": []}
    paper_obj = None
    if entry["doi"]:
        try:
            paper_obj = await orchestrator._get_openalex(entry["doi"])
        except Exception:
            pass
    if not paper_obj and entry["title"]:
        try:
            papers = await orchestrator._search_openalex(query=entry["title"], limit=1)
            if papers:
                paper_obj = papers[0]
        except Exception:
            pass
    if not paper_obj:
        result["status"] = "ABSENT"
        result["issues"].append("Introuvable")
        return result
    p = paper_obj.to_dict()
    sim = difflib.SequenceMatcher(
        None, clean_latex(entry["title"]), clean_latex(p.get("title", ""))
    ).ratio()
    if sim < 0.92:
        result["status"] = "TITRE"
        result["issues"].append(f"Titre diff ({int(sim * 100)}%)")
    r_year = str(p.get("year", ""))
    if entry["year"] and r_year != "None" and entry["year"] != r_year:
        if abs(int(entry["year"]) - int(r_year)) > 1:
            result["status"] = "ANNEE"
            result["issues"].append(f"Annee: {entry['year']}->{r_year}")
    if not entry["doi"] and p.get("doi") and sim > 0.95:
        result["status"] = "DOI"
        result["issues"].append(f"DOI manquant: {p.get('doi')}")
    return result


async def main():
    bib_path = r"D:\Github\Revue-de-litterature---Maitrise\references.bib"
    orchestrator = Orchestrator(openalex_mailto="tofunori@gmail.com")
    entries = parse_bib_file(bib_path)

    report = [
        "# Audit Qualité de la Bibliographie\n",
        f"Date : {datetime.now().strftime('%Y-%m-%d')}\n",
        "| Clé | État | Diagnostic |",
        "| :--- | :--- | :--- |",
    ]

    print(f"Audit de {len(entries)} références...")
    for i, entry in enumerate(entries):
        res = await audit_entry(entry, orchestrator)
        icon = {
            "OK": "✅ OK",
            "ABSENT": "❌ Absent",
            "TITRE": "⚠️ Titre",
            "ANNEE": "📅 Année",
            "DOI": "🆔 DOI",
        }.get(res["status"], res["status"])
        report.append(f"| {res['key']} | {icon} | {', '.join(res['issues'])} |")
        if (i + 1) % 50 == 0:
            print(f"Progression : {i + 1}/{len(entries)}")
        await asyncio.sleep(0.05)

    with open("temp_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(report))


if __name__ == "__main__":
    asyncio.run(main())
