"""Download candidate ABM questions from upstream and audit structure. Never auto-publish."""
import hashlib
import io
import json
import pathlib
import urllib.request
import zipfile

SOURCE = "https://github.com/etspring/pdd_russia/archive/refs/heads/master.zip"
DEST = pathlib.Path("data/abm_candidate")

def run(blob: bytes):
    questions, missing, errors, images = [], [], [], set()
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        files = {tuple(pathlib.PurePosixPath(p).parts[1:]): p
                 for p in archive.namelist() if not p.endswith("/")}
        for ticket in range(1, 41):
            key = ("questions", "A_B", "tickets", f"Билет {ticket}.json")
            name = files.get(key)
            if not name:
                missing.append(f"Билет {ticket}.json")
                continue
            rows = json.loads(archive.read(name))
            if not isinstance(rows, list) or len(rows) != 20:
                errors.append(f"Билет {ticket}: not exactly 20 questions")
                continue
            for pos, q in enumerate(rows, 1):
                answers = q.get("answers", [])
                correct = [i for i, a in enumerate(answers) if a.get("is_correct") is True]
                if len(correct) != 1 or len(answers) < 2:
                    errors.append(f"{ticket}/{pos}: invalid answer key")
                    continue
                image = q.get("image")
                if image:
                    image = image.replace("\\", "/").removeprefix("./").removeprefix("tmp/")
                    if image == "images/no_image.jpg":
                        image = None
                    elif ".." in pathlib.PurePosixPath(image).parts or not image.startswith("images/A_B/"):
                        errors.append(f"{ticket}/{pos}: unsafe image path")
                        image = None
                    else:
                        images.add(image)
                questions.append({
                    "id": f"ABM-{ticket:02d}-{pos:02d}", "ticket": ticket,
                    "position": pos, "block": (pos - 1) // 5 + 1,
                    "topic": q.get("topic", []), "text": q.get("question", ""),
                    "choices": [a.get("answer_text", "") for a in answers],
                    "correct": correct[0], "image": image,
                    "explanation": q.get("answer_tip", ""),
                    "source_id": q.get("id"),
                })
        for rel in images:
            name = files.get(tuple(pathlib.PurePosixPath(rel).parts))
            if name is None:
                missing.append(rel)
                continue
            path = DEST / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(name))
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / "questions.json").write_text(json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8")
    audit = {
        "source": SOURCE, "source_sha256": hashlib.sha256(blob).hexdigest(),
        "ticket_count": len({q["ticket"] for q in questions}),
        "question_count": len(questions), "image_count": len(images),
        "missing": missing, "errors": errors,
        "answer_key_verified_against_official": False,
        "rights_verified": False,
    }
    (DEST / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit

if __name__ == "__main__":
    request = urllib.request.Request(SOURCE, headers={"User-Agent": "N13-PDD-Audit/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        audit = run(response.read())
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    if audit["question_count"] != 800 or audit["ticket_count"] != 40 or audit["missing"] or audit["errors"]:
        raise SystemExit("Candidate dataset incomplete. Inspect audit.json")
    print("Candidate structurally complete. Official validation still required.")
