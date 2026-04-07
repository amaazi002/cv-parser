import os
import io
import re
import json
import base64
import logging

import spacy
from flask import Flask, request, jsonify
import pdfplumber
from docx import Document

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ══════════════════════════════════════════════
# CHARGEMENT MODÈLE NLP
# ══════════════════════════════════════════════

try:
    nlp = spacy.load('fr_core_news_sm')
    logging.info("✅ Modèle spaCy chargé")
except Exception as e:
    logging.error(f"❌ Erreur chargement spaCy: {e}")
    nlp = None

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
# DÉCOUPEUR DE SECTIONS
# ══════════════════════════════════════════════

def split_sections(text):
    """
    Découpe le CV en sections selon les titres détectés
    """
    sections = {
        'experience'  : '',
        'formation'   : '',
        'competences' : '',
        'profil'      : '',
        'autres'      : ''
    }

    # Patterns des titres de sections
    section_patterns = {
        'experience': [
            r'exp[eé]riences?\s*professionnelles?',
            r'parcours\s*professionnel',
            r'historique\s*professionnel',
            r'emplois?',
            r'postes?\s*occup[eé]s?',
            r'work\s*experience',
            r'professional\s*experience',
            r'employment'
        ],
        'formation': [
            r'formations?\s*(?:acad[eé]mique)?',
            r'[eé]ducation',
            r'dipl[oô]mes?',
            r'[eé]tudes?',
            r'parcours\s*acad[eé]mique',
            r'cursus',
            r'education',
            r'academic\s*background'
        ],
        'competences': [
            r'comp[eé]tences?\s*(?:techniques?|professionnelles?|cls[eé]s?)?',
            r'savoir[\s\-]faire',
            r'expertise',
            r'technologies?',
            r'outils?',
            r'skills?',
            r'technical\s*skills?'
        ],
        'profil': [
            r'profil',
            r'[àa]\s*propos',
            r'r[eé]sum[eé]',
            r'objectif',
            r'pr[eé]sentation',
            r'summary',
            r'profile',
            r'about\s*me'
        ]
    }

    lines      = text.split('\n')
    current    = 'autres'
    buffer     = {k: [] for k in sections}

    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            buffer[current].append('')
            continue

        matched = False
        for section_name, patterns in section_patterns.items():
            for pat in patterns:
                if re.match(r'^\s*' + pat + r'\s*:?\s*$', line_clean, re.IGNORECASE):
                    current = section_name
                    matched = True
                    break
            if matched:
                break

        if not matched:
            buffer[current].append(line_clean)

    for k in sections:
        sections[k] = '\n'.join(buffer[k]).strip()

    return sections

# ══════════════════════════════════════════════
# PARSEURS NLP
# ══════════════════════════════════════════════

def parse_experience_prof_nlp(text_experience, text_complet):
    """
    Extrait les expériences professionnelles avec NLP
    """
    source = text_experience if text_experience.strip() else text_complet

    if not source.strip():
        return ''

    lignes   = [l.strip() for l in source.split('\n') if l.strip()]
    resultats = []
    current_job = []

    # Patterns pour détecter début d'un poste
    date_pattern = re.compile(
        r'\b(\d{4})\s*[-–—]\s*(\d{4}|présent|present|actuel|aujourd\'hui|en\s*cours)\b',
        re.IGNORECASE
    )
    mois_pattern = re.compile(
        r'\b(jan|fév|feb|mar|avr|apr|mai|may|jun|jul|ao[uû]|aug|sep|oct|nov|d[eé]c|dec)\w*'
        r'\s*\.?\s*\d{4}',
        re.IGNORECASE
    )

    for ligne in lignes:
        is_new_job = bool(date_pattern.search(ligne)) or bool(mois_pattern.search(ligne))

        if is_new_job and current_job:
            resultats.append(' | '.join(current_job))
            current_job = [ligne]
        elif is_new_job:
            current_job = [ligne]
        elif current_job:
            current_job.append(ligne)

    if current_job:
        resultats.append(' | '.join(current_job))

    # Si NLP disponible → enrichir avec entités nommées
    if nlp and source:
        try:
            doc      = nlp(source[:5000])
            orgs     = list({
                ent.text.strip()
                for ent in doc.ents
                if ent.label_ == 'ORG' and len(ent.text.strip()) > 2
            })
            if orgs and not resultats:
                resultats = orgs[:5]
        except Exception as e:
            logging.warning(f"NLP experience: {e}")

    return '\n'.join(resultats) if resultats else source[:800]


def parse_competences_tech_nlp(text_competences, text_complet):
    """
    Extrait les compétences techniques avec NLP
    """
    source = text_competences if text_competences.strip() else text_complet

    if not source.strip():
        return ''

    lignes    = [l.strip() for l in source.split('\n') if l.strip()]
    competences = []

    # Patterns pour lignes de compétences
    separateurs = re.compile(r'[,;|•·\-–/]')

    for ligne in lignes:
        # Ignorer les titres
        if re.match(r'^(compétences?|skills?|techniques?|outils?|technologies?)[\s:]*$',
                    ligne, re.IGNORECASE):
            continue

        # Si la ligne contient des séparateurs → liste de compétences
        if separateurs.search(ligne):
            items = separateurs.split(ligne)
            for item in items:
                item = item.strip()
                if item and len(item) > 1 and len(item) < 60:
                    competences.append(item)
        elif len(ligne) < 80:
            competences.append(ligne)

    # Dédupliquer en gardant l'ordre
    seen = set()
    unique = []
    for c in competences:
        c_lower = c.lower()
        if c_lower not in seen and len(c.strip()) > 1:
            seen.add(c_lower)
            unique.append(c)

    return ', '.join(unique) if unique else ''


def parse_competences_perso_nlp(text_profil, text_complet):
    """
    Extrait les compétences personnelles / soft skills avec NLP
    """
    source = text_profil if text_profil.strip() else text_complet

    if not source.strip():
        return ''

    lignes     = [l.strip() for l in source.split('\n') if l.strip()]
    soft_skills = []

    separateurs = re.compile(r'[,;|•·\-–/]')

    for ligne in lignes:
        if re.match(r'^(compétences?\s*perso|soft\s*skills?|qualit[eé]s?)[\s:]*$',
                    ligne, re.IGNORECASE):
            continue
        if separateurs.search(ligne):
            items = separateurs.split(ligne)
            for item in items:
                item = item.strip()
                if item and len(item) > 2 and len(item) < 60:
                    soft_skills.append(item)
        elif len(ligne) < 80:
            soft_skills.append(ligne)

    seen   = set()
    unique = []
    for s in soft_skills:
        s_lower = s.lower()
        if s_lower not in seen:
            seen.add(s_lower)
            unique.append(s)

    return ', '.join(unique) if unique else ''


def parse_formation_nlp(text_formation, text_complet):
    """
    Extrait diplôme, école et année avec NLP
    """
    source = text_formation if text_formation.strip() else text_complet

    diplome      = ''
    ecole        = ''
    annee        = None

    if not source.strip():
        return diplome, ecole, annee

    lignes = [l.strip() for l in source.split('\n') if l.strip()]

    # Patterns diplômes
    diplome_patterns = [
        r'(master\s*\d*\s*[^\n]{3,80})',
        r'(mastère\s*[^\n]{3,80})',
        r'(licence\s*[^\n]{3,80})',
        r'(bachelor\s*[^\n]{3,80})',
        r'(bts\s*[^\n]{3,80})',
        r'(dut\s*[^\n]{3,80})',
        r'(doctorat\s*[^\n]{3,80})',
        r'(phd\s*[^\n]{3,80})',
        r'(ing[eé]nieur\s*[^\n]{3,80})',
        r'(mba\s*[^\n]{3,80})',
        r'(bac\s*\+\s*\d\s*[^\n]{0,80})',
        r'(dipl[oô]me\s*[^\n]{3,80})',
        r'(certificat\s*[^\n]{3,80})',
        r'(formation\s*[^\n]{3,80})',
    ]

    # Patterns écoles
    ecole_patterns = [
        r'(universit[eé]\s*[^\n,;.]{3,80})',
        r'([eé]cole\s*(?:nationale|sup[eé]rieure|polytechnique|de|d\'|des|du)?\s*[^\n,;.]{3,80})',
        r'(institut\s*[^\n,;.]{3,80})',
        r'(iut\s*[^\n,;.]{3,80})',
        r'(insa\s*[^\n,;.]{3,60})',
        r'(esc\s*[^\n,;.]{3,60})',
        r'(hec\s*[^\n,;.]{0,60})',
        r'(sup[eé]lec\s*[^\n,;.]{0,60})',
        r'(polytechnique\s*[^\n,;.]{0,60})',
        r'(grande\s*[eé]cole\s*[^\n,;.]{0,60})',
        r'(faculty\s*[^\n,;.]{3,80})',
        r'(college\s*[^\n,;.]{3,80})',
    ]

    for ligne in lignes:
        # Chercher diplôme
        if not diplome:
            for pat in diplome_patterns:
                m = re.search(pat, ligne, re.IGNORECASE)
                if m:
                    diplome = m.group(1).strip()[:120]
                    break

        # Chercher école
        if not ecole:
            for pat in ecole_patterns:
                m = re.search(pat, ligne, re.IGNORECASE)
                if m:
                    ecole = m.group(1).strip()[:120]
                    break

    # NLP pour les organisations (écoles)
    if nlp and not ecole:
        try:
            doc  = nlp(source[:3000])
            orgs = [
                ent.text.strip()
                for ent in doc.ents
                if ent.label_ == 'ORG' and len(ent.text.strip()) > 3
            ]
            if orgs:
                ecole = orgs[0]
        except Exception as e:
            logging.warning(f"NLP ecole: {e}")

    # Chercher l'année dans la section formation
    years = re.findall(r'\b(20\d{2}|19\d{2})\b', source)
    if years:
        annee = max(int(y) for y in years)

    return diplome, ecole, annee


def parse_annees_experience_nlp(text_experience, text_complet):
    """
    Calcule les années d'expérience depuis les dates trouvées
    """
    source = text_experience if text_experience.strip() else text_complet

    # Chercher mention directe
    mention = re.search(
        r'(\d+)\s*(?:\+\s*)?ans?\s*d[\'e]?\s*exp[eé]riences?',
        source, re.IGNORECASE
    )
    if mention:
        val = int(mention.group(1))
        if 0 < val <= 50:
            return val

    # Calculer depuis les plages de dates
    date_ranges = re.findall(
        r'(\d{4})\s*[-–—]\s*(\d{4}|présent|present|actuel|aujourd\'hui|en\s*cours)',
        source, re.IGNORECASE
    )

    import datetime
    annee_courante = datetime.datetime.now().year
    total_mois     = 0

    for debut_str, fin_str in date_ranges:
        debut = int(debut_str)
        fin_lower = fin_str.lower().strip()
        if any(w in fin_lower for w in ['présent', 'present', 'actuel', "aujourd", 'cours']):
            fin = annee_courante
        else:
            try:
                fin = int(fin_str)
            except ValueError:
                fin = annee_courante

        if 1970 <= debut <= annee_courante and debut <= fin:
            total_mois += (fin - debut) * 12

    if total_mois > 0:
        annees = round(total_mois / 12)
        return min(annees, 50)

    return 0

# ══════════════════════════════════════════════
# ROUTE PRINCIPALE
# ══════════════════════════════════════════════

@app.route('/parse-cv', methods=['POST'])
def parse_cv():
    try:
        data = request.get_json(force=True, silent=True)

        if not data:
            logging.error("Body JSON vide")
            return jsonify({'error': 'Body JSON requis'}), 400

        file_base64  = data.get('fileBase64', '')
        file_name    = data.get('fileName', '').lower().strip()
        content_type = data.get('contentType', '').lower().strip()

        logging.info(f"Fichier: {file_name} | Type: {content_type}")

        if not file_base64:
            return jsonify({'error': 'fileBase64 manquant'}), 400

        # Nettoyer le base64
        if ',' in file_base64:
            file_base64 = file_base64.split(',')[1]

        file_base64 = file_base64.strip().replace(' ', '+').replace('\n', '').replace('\r', '').replace('\t', '')

        missing_padding = len(file_base64) % 4
        if missing_padding:
            file_base64 += '=' * (4 - missing_padding)

        try:
            file_bytes = base64.b64decode(file_base64, validate=False)
            logging.info(f"Bytes décodés: {len(file_bytes)}")
        except Exception as e:
            return jsonify({'error': f'Base64 invalide: {str(e)}'}), 400

        # Détecter le type
        is_pdf  = 'pdf' in content_type or file_name.endswith('.pdf')
        is_docx = 'officedocument' in content_type or file_name.endswith('.docx')
        is_doc  = 'msword' in content_type or file_name.endswith('.doc')

        if not is_pdf and not is_docx and not is_doc:
            if file_bytes[:4] == b'%PDF':
                is_pdf = True
            elif file_bytes[:2] == b'PK':
                is_docx = True

        # Extraire le texte
        text = ""
        if is_pdf:
            text = extract_text_from_pdf(file_bytes)
        elif is_docx:
            text = extract_text_from_docx(file_bytes)
        elif is_doc:
            text = extract_text_from_doc(file_bytes)
        else:
            return jsonify({'error': 'Format non supporté'}), 400

        logging.info(f"Texte extrait: {len(text)} caractères")
        logging.info(f"Aperçu texte: {text[:200]}")

        if not text.strip():
            return jsonify({
                'anneesExperience': 0,
                'competencesTech' : '',
                'experienceProf'  : '',
                'competencesPerso': '',
                'dernierDiplome'  : '',
                'ecoleUniversite' : '',
                'anneeObtention'  : None,
                'warning'         : 'Impossible d\'extraire le texte. CV peut être scanné.'
            }), 200

        # Découper en sections
        sections = split_sections(text)
        logging.info(f"Sections trouvées: { {k: len(v) for k, v in sections.items()} }")

        # Parser avec NLP
        diplome, ecole, annee = parse_formation_nlp(
            sections['formation'], text
        )

        result = {
            'anneesExperience': parse_annees_experience_nlp(sections['experience'], text),
            'competencesTech' : parse_competences_tech_nlp(sections['competences'], text),
            'experienceProf'  : parse_experience_prof_nlp(sections['experience'], text),
            'competencesPerso': parse_competences_perso_nlp(sections['profil'], text),
            'dernierDiplome'  : diplome,
            'ecoleUniversite' : ecole,
            'anneeObtention'  : annee,
        }

        logging.info(f"Résultat: {json.dumps(result, ensure_ascii=False)[:300]}")
        return jsonify(result), 200

    except Exception as e:
        logging.error(f"Erreur: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status' : 'ok',
        'message': 'CV Parser API running',
        'nlp'    : 'loaded' if nlp else 'not loaded'
    }), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)