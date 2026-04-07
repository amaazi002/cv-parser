import os
import io
import re
import json
import base64
import logging

from flask import Flask, request, jsonify
import pdfplumber
from docx import Document

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ══════════════════════════════════════════════
# EXTRACTEURS DE TEXTE
# ══════════════════════════════════════════════

def extract_text_from_pdf(file_bytes):
    text = ""
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        logging.error(f"Erreur PDF: {e}")
    return text

def extract_text_from_docx(file_bytes):
    text = ""
    try:
        doc = Document(io.BytesIO(file_bytes))
        for para in doc.paragraphs:
            if para.text.strip():
                text += para.text + "\n"
    except Exception as e:
        logging.error(f"Erreur DOCX: {e}")
    return text

def extract_text_from_doc(file_bytes):
    try:
        text = file_bytes.decode('utf-8', errors='ignore')
        text = re.sub(r'[^\x20-\x7E\n\r\t\u00C0-\u024F]', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        return text
    except Exception as e:
        logging.error(f"Erreur DOC: {e}")
        return ""

# ══════════════════════════════════════════════
# PARSEURS DE DONNÉES
# ══════════════════════════════════════════════

def parse_annees_experience(text):
    patterns = [
        r'(\d+)\s*(?:ans?|années?)\s*d[\'e]expérience',
        r'expérience\s*(?:de\s*)?(\d+)\s*ans?',
        r'(\d+)\s*years?\s*(?:of\s*)?experience',
        r'(\d+)\+?\s*ans?\s*d[\'e]xp',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            val = int(match.group(1))
            if 0 <= val <= 50:
                return val
    return 0

def parse_competences_tech(text):
    tech_keywords = [
        'Python','Java','JavaScript','TypeScript','C#','C++','PHP',
        'Ruby','Swift','Kotlin','Go','Rust','Scala','R',
        'React','Vue','Angular','HTML','CSS','SASS','Bootstrap',
        'Tailwind','jQuery','Next.js','Nuxt',
        'Node.js','Django','Flask','Spring','Laravel','Express',
        'FastAPI','Rails','ASP.NET',
        'MySQL','PostgreSQL','MongoDB','Oracle','SQL Server',
        'Redis','Elasticsearch','SQLite','MariaDB',
        'AWS','Azure','GCP','Docker','Kubernetes','Jenkins',
        'CI/CD','Terraform','Ansible','Linux','Git','GitHub',
        'Salesforce','Apex','LWC','SOQL','Force.com',
        'Machine Learning','Deep Learning','TensorFlow','PyTorch',
        'Pandas','NumPy','Scikit-learn','Power BI','Tableau',
        'Android','iOS','React Native','Flutter','Xamarin',
        'REST','GraphQL','Microservices','Agile','Scrum','JIRA'
    ]
    found = []
    for kw in tech_keywords:
        if re.search(r'\b' + re.escape(kw) + r'\b', text, re.IGNORECASE):
            found.append(kw)
    return ', '.join(found) if found else ''

def parse_experience_prof(text):
    exp_patterns = [
        r'(?:EXPÉRIENCE|EXPERIENCE|PARCOURS\s*PROFESSIONNEL)[^\n]*\n(.*?)(?=FORMATION|COMPÉTENCES|ÉDUCATION|$)',
        r'(?:WORK\s*EXPERIENCE|EMPLOYMENT)[^\n]*\n(.*?)(?=EDUCATION|SKILLS|$)',
    ]
    for pattern in exp_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            section = match.group(1).strip()
            lines   = [l.strip() for l in section.split('\n') if l.strip()]
            return '\n'.join(lines[:10])

    job_patterns = [
        r'(\d{4}\s*[-–]\s*(?:\d{4}|présent|present|actuel).*)',
        r'((?:jan|fév|mar|avr|mai|jun|jul|aoû|sep|oct|nov|déc)\w*\.?\s*\d{4}.*)',
    ]
    jobs = []
    for pattern in job_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        jobs.extend(matches[:5])
    return '\n'.join(jobs) if jobs else ''

def parse_competences_perso(text):
    soft_keywords = [
        'leadership','communication','travail en équipe','teamwork',
        'autonomie','autonome','rigoureux','rigueur','créativité',
        'créatif','adaptabilité','adaptable','ponctuel','ponctualité',
        'organisé','organisation','proactif','initiative','dynamique',
        'enthousiaste','motivé','curieux','analytique','réactif',
        'persévérant','esprit critique','problem solving',
        'gestion du stress','gestion du temps','time management',
        'empathie','écoute','négociation','présentation','rédaction',
        'prise de décision','management','force de proposition',
        'sens des responsabilités'
    ]
    found = []
    for kw in soft_keywords:
        if re.search(r'\b' + re.escape(kw) + r'\b', text, re.IGNORECASE):
            found.append(kw.capitalize())
    return ', '.join(found) if found else ''

def parse_diplome(text):
    diplome_patterns = [
        r'(Master\s+\d*\s+[^\n,;.]{3,60})',
        r'(Mastère\s+[^\n,;.]{3,60})',
        r'(Licence\s+[^\n,;.]{3,60})',
        r'(Bachelor\s+[^\n,;.]{3,60})',
        r'(BTS\s+[^\n,;.]{3,60})',
        r'(DUT\s+[^\n,;.]{3,60})',
        r'(Doctorat\s+[^\n,;.]{3,60})',
        r'(PhD\s+[^\n,;.]{3,60})',
        r'(Ingénieur\s+[^\n,;.]{3,60})',
        r'(MBA\s+[^\n,;.]{3,60})',
        r'(Bac\s*\+\s*\d\s+[^\n,;.]{3,60})',
    ]
    for pattern in diplome_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()[:100]
    return ''

def parse_ecole(text):
    ecole_patterns = [
        r'(?:université|university|univ\.?)\s+([^\n,;.]{3,60})',
        r'(?:école|ecole|school|institute|institut)\s+(?:nationale|supérieure|de|d\'|des|du)?\s*([^\n,;.]{3,60})',
        r'(INSA\s+[^\n,;.]{3,40})',
        r'(ESC\s+[^\n,;.]{3,40})',
        r'(IUT\s+[^\n,;.]{3,40})',
    ]
    for pattern in ecole_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            grp = match.group(1) if match.lastindex and match.lastindex >= 1 else match.group(0)
            return grp.strip()[:100]
    return ''

def parse_annee_obtention(text):
    formation_block = re.search(
        r'(?:FORMATION|EDUCATION|DIPLÔME)[^\n]*\n(.*?)(?=EXPÉRIENCE|EXPERIENCE|COMPÉTENCES|$)',
        text, re.IGNORECASE | re.DOTALL
    )
    if formation_block:
        block = formation_block.group(1)
        years = re.findall(r'\b(20\d{2}|19\d{2})\b', block)
        if years:
            return max(int(y) for y in years)

    all_years = re.findall(r'\b(20\d{2}|19\d{2})\b', text)
    if all_years:
        return max(int(y) for y in all_years)
    return None

# ══════════════════════════════════════════════
# ROUTE PRINCIPALE - CORRIGÉE
# ══════════════════════════════════════════════

@app.route('/parse-cv', methods=['POST'])
def parse_cv():
    try:
        data = request.get_json(force=True, silent=True)
        
        if not data:
            logging.error("Body JSON vide ou invalide")
            return jsonify({'error': 'Body JSON requis'}), 400

        file_base64  = data.get('fileBase64', '')
        file_name    = data.get('fileName', '').lower().strip()
        content_type = data.get('contentType', '').lower().strip()

        logging.info(f"Fichier reçu: {file_name} | Type: {content_type}")
        logging.info(f"Base64 longueur brute: {len(file_base64)}")

        if not file_base64:
            return jsonify({'error': 'fileBase64 manquant'}), 400

        # ✅ Nettoyer le base64 - enlever le préfixe data:...;base64,
        if ',' in file_base64:
            file_base64 = file_base64.split(',')[1]

        # ✅ Nettoyer les caractères indésirables
        file_base64 = file_base64.strip()
        file_base64 = file_base64.replace(' ', '+')
        file_base64 = file_base64.replace('\n', '')
        file_base64 = file_base64.replace('\r', '')
        file_base64 = file_base64.replace('\t', '')

        # ✅ Ajouter le padding manquant
        missing_padding = len(file_base64) % 4
        if missing_padding:
            file_base64 += '=' * (4 - missing_padding)

        logging.info(f"Base64 longueur nettoyée: {len(file_base64)}")

        try:
            file_bytes = base64.b64decode(file_base64, validate=False)
            logging.info(f"Bytes décodés: {len(file_bytes)}")
        except Exception as e:
            logging.error(f"Erreur décodage base64: {e}")
            return jsonify({'error': f'Base64 invalide: {str(e)}'}), 400

        if len(file_bytes) < 100:
            return jsonify({'error': 'Fichier trop petit ou corrompu'}), 400

        # ✅ Détecter le type de fichier
        text = ""
        is_pdf  = 'pdf' in content_type or file_name.endswith('.pdf')
        is_docx = 'officedocument' in content_type or file_name.endswith('.docx')
        is_doc  = 'msword' in content_type or file_name.endswith('.doc')

        # ✅ Détection par magic bytes si content_type vide
        if not is_pdf and not is_docx and not is_doc:
            if file_bytes[:4] == b'%PDF':
                is_pdf = True
            elif file_bytes[:2] == b'PK':
                is_docx = True

        if is_pdf:
            logging.info("Extraction PDF...")
            text = extract_text_from_pdf(file_bytes)
        elif is_docx:
            logging.info("Extraction DOCX...")
            text = extract_text_from_docx(file_bytes)
        elif is_doc:
            logging.info("Extraction DOC...")
            text = extract_text_from_doc(file_bytes)
        else:
            logging.error(f"Format non reconnu: {content_type} / {file_name}")
            return jsonify({'error': f'Format non supporté: {content_type}'}), 400

        logging.info(f"Texte extrait: {len(text)} caractères")

        if not text.strip():
            logging.warning("Texte vide après extraction")
            return jsonify({
                'anneesExperience': 0,
                'competencesTech' : '',
                'experienceProf'  : '',
                'competencesPerso': '',
                'dernierDiplome'  : '',
                'ecoleUniversite' : '',
                'anneeObtention'  : None,
                'warning'         : 'Texte non extrait du CV'
            }), 200

        # ✅ Parser les données
        result = {
            'anneesExperience': parse_annees_experience(text),
            'competencesTech' : parse_competences_tech(text),
            'experienceProf'  : parse_experience_prof(text),
            'competencesPerso': parse_competences_perso(text),
            'dernierDiplome'  : parse_diplome(text),
            'ecoleUniversite' : parse_ecole(text),
            'anneeObtention'  : parse_annee_obtention(text),
        }

        logging.info(f"Résultat: {json.dumps(result, ensure_ascii=False)}")
        return jsonify(result), 200

    except Exception as e:
        logging.error(f"Erreur inattendue: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'message': 'CV Parser API running'}), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)