"""Populate persistent data directory with audited candidate ABM dataset."""
import os
from pathlib import Path
from tools.fetch_abm import SOURCE, run
import urllib.request

if __name__ == "__main__":
    dest = Path(os.getenv("PDD_DATA_ROOT", "data"))
    # importer currently writes to data/abm_candidate relative to working dir
    if dest.resolve() != Path("data").resolve():
        raise SystemExit("Run importer in repository directory with the default data root")
    if (dest / "abm_candidate" / "audit.json").exists():
        from app.question_bank import QuestionBank
        QuestionBank(dest / "abm_candidate")
        print("Existing audited candidate bank found; preserving persistent data")
        raise SystemExit(0)
    request = urllib.request.Request(SOURCE, headers={"User-Agent": "N13-PDD-Installer/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        result = run(response.read())
    if result["question_count"] != 800 or result["errors"] or result["missing"]:
        raise SystemExit("ABM audit failed")
    print("Candidate bank prepared, official content verification outstanding")
