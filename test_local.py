import base64
import json
import requests

# ══════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════

CV_PATH = r"C:\Users\amaazi002\Downloads\cvtest.pdf" 
API_URL = "http://localhost:5000"

# ══════════════════════════════════════
# TEST PARSE CV
# ══════════════════════════════════════

def test_parse_cv(cv_path):
    print("=" * 50)
    print("TEST /parse-cv")
    print("=" * 50)

    # Lire et encoder le fichier
    with open(cv_path, "rb") as f:
        file_bytes  = f.read()
        file_base64 = base64.b64encode(file_bytes).decode("utf-8")

    # Détecter le type
    ext = cv_path.split(".")[-1].lower()
    content_types = {
        "pdf"  : "application/pdf",
        "docx" : "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "doc"  : "application/msword"
    }
    content_type = content_types.get(ext, "application/pdf")
    file_name    = cv_path.split("\\")[-1].split("/")[-1]

    print(f"Fichier     : {file_name}")
    print(f"Type        : {content_type}")
    print(f"Taille b64  : {len(file_base64)} caractères")

    # Appel API
    response = requests.post(
        f"{API_URL}/parse-cv",
        json={
            "fileBase64"  : file_base64,
            "fileName"    : file_name,
            "contentType" : content_type
        }
    )

    print(f"\nStatus : {response.status_code}")
    result = response.json()
    print(f"\nRésultat :")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    return result

# ══════════════════════════════════════
# TEST MATCH CV
# ══════════════════════════════════════

def test_match_cv(cv_data):
    print("\n" + "=" * 50)
    print("TEST /match-cv")
    print("=" * 50)

    response = requests.post(
        f"{API_URL}/match-cv",
        json={
            "cvData"              : cv_data,
            "competencesRequises" : "Python, Django, SQL, REST API, Git",
            "descriptionOffre"    : "Développeur Python Django avec 3 ans d'expérience minimum"
        }
    )

    print(f"Status : {response.status_code}")
    result = response.json()
    print(f"\nRésultat :")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    return result

# ══════════════════════════════════════
# MAIN
# ══════════════════════════════════════

if __name__ == "__main__":
    import sys

    # ✅ Récupérer le path depuis les arguments
    if len(sys.argv) > 1:
        cv_path = sys.argv[1]
    else:
        cv_path = CV_PATH

    print(f"\n🚀 Test avec le fichier : {cv_path}\n")

    # Test 1 — Parsing
    cv_data = test_parse_cv(cv_path)

    # Test 2 — Matching (si parsing OK)
    if "error" not in cv_data:
        test_match_cv(cv_data)
    else:
        print(f"\n❌ Parsing échoué : {cv_data.get('error')}")