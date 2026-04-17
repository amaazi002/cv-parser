import os
import io
import re
import json
import base64
import logging
import datetime

from flask import Flask, request, jsonify
import pdfplumber
from docx import Document

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ══════════════════════════════════════════════
# CHARGEMENT MODÈLE NLP SPACY
# ══════════════════════════════════════════════

try:
    import spacy
    nlp = spacy.load('fr_core_news_md')
    logging.info("✅ Modèle SpaCy fr_core_news_md chargé")
except Exception as e:
    logging.error(f"❌ Erreur chargement SpaCy: {e}")
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
    sections = {
        'experience'  : '',
        'formation'   : '',
        'competences' : '',
        'profil'      : '',
        'autres'      : ''
    }

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
            r'comp[eé]tences?\s*(?:techniques?|professionnelles?|cl[eé]s?)?',
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

    lines   = text.split('\n')
    current = 'autres'
    buffer  = {k: [] for k in sections}

    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            buffer[current].append('')
            continue

        matched = False
        for section_name, patterns in section_patterns.items():
            for pat in patterns:
                if re.match(r'^\s*' + pat + r'\s*:?\s*$',
                            line_clean, re.IGNORECASE):
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
# PARSEURS AVEC SPACY + REGEX
# ══════════════════════════════════════════════

def parse_experience_prof_nlp(text_experience, text_complet):
    """
    Extrait les expériences professionnelles
    SpaCy → détecter organisations et titres de postes
    Regex → détecter les dates
    """
    source = text_experience if text_experience.strip() else text_complet
    if not source.strip():
        return ''

    lignes      = [l.strip() for l in source.split('\n') if l.strip()]
    resultats   = []
    current_job = []

    # ✅ Regex dates
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
        is_new_job = (bool(date_pattern.search(ligne)) or
                      bool(mois_pattern.search(ligne)))

        if is_new_job and current_job:
            resultats.append(' | '.join(current_job))
            current_job = [ligne]
        elif is_new_job:
            current_job = [ligne]
        elif current_job:
            current_job.append(ligne)

    if current_job:
        resultats.append(' | '.join(current_job))

    # ✅ SpaCy — Enrichir avec organisations et postes
    if nlp and source:
        try:
            doc = nlp(source[:5000])

            # Extraire organisations (entreprises)
            orgs = list({
                ent.text.strip()
                for ent in doc.ents
                if ent.label_ == 'ORG'
                and len(ent.text.strip()) > 2
            })

            # Extraire personnes (titres de postes parfois détectés)
            # et lieux (villes de travail)
            lieux = list({
                ent.text.strip()
                for ent in doc.ents
                if ent.label_ in ('LOC', 'GPE')
                and len(ent.text.strip()) > 2
            })

            logging.info(f"SpaCy orgs trouvées: {orgs[:5]}")
            logging.info(f"SpaCy lieux trouvés: {lieux[:3]}")

            # Si regex n'a rien trouvé → utiliser SpaCy comme fallback
            if not resultats and orgs:
                resultats = orgs[:5]

        except Exception as e:
            logging.warning(f"SpaCy experience: {e}")

    return '\n'.join(resultats) if resultats else source[:800]


def parse_competences_tech_nlp(text_competences, text_complet):
    """
    Extrait les compétences techniques
    SpaCy → détecter les entités technologiques
    Regex → découper par séparateurs
    """
    source = text_competences if text_competences.strip() else text_complet
    if not source.strip():
        return ''

    lignes      = [l.strip() for l in source.split('\n') if l.strip()]
    competences = []
    separateurs = re.compile(r'[,;|•·\-–/]')

    # ✅ Regex — découper par séparateurs
    for ligne in lignes:
        if re.match(
            r'^(compétences?|skills?|techniques?|outils?|technologies?)[\s:]*$',
            ligne, re.IGNORECASE
        ):
            continue

        if separateurs.search(ligne):
            items = separateurs.split(ligne)
            for item in items:
                item = item.strip()
                if item and 1 < len(item) < 60:
                    competences.append(item)
        elif len(ligne) < 80:
            competences.append(ligne)

    # ✅ SpaCy — Enrichir avec entités technologiques
    if nlp and source:
        try:
            doc = nlp(source[:3000])

            # Extraire les entités MISC (technologies, frameworks...)
            tech_spacy = [
                ent.text.strip()
                for ent in doc.ents
                if ent.label_ in ('MISC', 'ORG', 'PRODUCT')
                and len(ent.text.strip()) > 1
                and len(ent.text.strip()) < 50
            ]

            logging.info(f"SpaCy tech trouvées: {tech_spacy[:10]}")

            # Ajouter les entités SpaCy non déjà présentes
            competences_lower = {c.lower() for c in competences}
            for tech in tech_spacy:
                if tech.lower() not in competences_lower:
                    competences.append(tech)
                    competences_lower.add(tech.lower())

        except Exception as e:
            logging.warning(f"SpaCy competences: {e}")

    # Dédupliquer
    seen   = set()
    unique = []
    for c in competences:
        c_lower = c.lower()
        if c_lower not in seen and len(c.strip()) > 1:
            seen.add(c_lower)
            unique.append(c)

    return ', '.join(unique) if unique else ''


def parse_competences_perso_nlp(text_profil, text_complet):
    """
    Extrait les soft skills / compétences personnelles
    SpaCy → analyser le texte de profil
    Regex → découper par séparateurs
    """
    source = text_profil if text_profil.strip() else text_complet
    if not source.strip():
        return ''

    lignes      = [l.strip() for l in source.split('\n') if l.strip()]
    soft_skills = []
    separateurs = re.compile(r'[,;|•·\-–/]')

    # ✅ Regex
    for ligne in lignes:
        if re.match(
            r'^(compétences?\s*perso|soft\s*skills?|qualit[eé]s?)[\s:]*$',
            ligne, re.IGNORECASE
        ):
            continue

        if separateurs.search(ligne):
            items = separateurs.split(ligne)
            for item in items:
                item = item.strip()
                if item and 2 < len(item) < 60:
                    soft_skills.append(item)
        elif len(ligne) < 80:
            soft_skills.append(ligne)

    # ✅ SpaCy — Analyser le profil pour détecter les qualités
    if nlp and source:
        try:
            doc = nlp(source[:2000])

            # ✅ Liste de soft skills connus
            soft_keywords = {
                'autonomie', 'rigueur', 'curiosité', 'créativité',
                'leadership', 'communication', 'adaptabilité',
                'organisation', 'proactivité', 'empathie',
                'esprit', 'équipe', 'initiative', 'polyvalence',
                'motivation', 'dynamisme', 'persévérance',
                'analytical', 'problem', 'solving', 'teamwork',
                'management', 'gestion', 'coordination'
            }

            # Chercher les tokens qui correspondent aux soft skills
            for token in doc:
                token_lower = token.lemma_.lower()
                if (token_lower in soft_keywords and
                    token.pos_ in ('NOUN', 'ADJ') and
                    token_lower not in {s.lower() for s in soft_skills}):
                    soft_skills.append(token.text)

            logging.info(f"SpaCy soft skills: {soft_skills[:10]}")

        except Exception as e:
            logging.warning(f"SpaCy soft skills: {e}")

    # Dédupliquer
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
    Extrait diplôme, école et année
    SpaCy → détecter organisations (écoles) et dates
    Regex → patterns de diplômes
    """
    source = text_formation if text_formation.strip() else text_complet

    diplome = ''
    ecole   = ''
    annee   = None

    if not source.strip():
        return diplome, ecole, annee

    lignes = [l.strip() for l in source.split('\n') if l.strip()]

    # ✅ Patterns diplômes (Regex)
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
    ]

    # ✅ Patterns écoles (Regex)
    ecole_patterns = [
        r'(universit[eé]\s*[^\n,;.]{3,80})',
        r'([eé]cole\s*(?:nationale|sup[eé]rieure|polytechnique|de|d\'|des|du)?\s*[^\n,;.]{3,80})',
        r'(institut\s*[^\n,;.]{3,80})',
        r'(iut\s*[^\n,;.]{3,80})',
        r'(insa\s*[^\n,;.]{3,60})',
        r'(hec\s*[^\n,;.]{0,60})',
        r'(polytechnique\s*[^\n,;.]{0,60})',
    ]

    for ligne in lignes:
        if not diplome:
            for pat in diplome_patterns:
                m = re.search(pat, ligne, re.IGNORECASE)
                if m:
                    diplome = m.group(1).strip()[:120]
                    break

        if not ecole:
            for pat in ecole_patterns:
                m = re.search(pat, ligne, re.IGNORECASE)
                if m:
                    ecole = m.group(1).strip()[:120]
                    break

    # ✅ SpaCy — Détecter les organisations (écoles)
    if nlp:
        try:
            doc = nlp(source[:3000])

            # Extraire organisations
            orgs = [
                ent.text.strip()
                for ent in doc.ents
                if ent.label_ == 'ORG'
                and len(ent.text.strip()) > 3
            ]

            # Extraire dates SpaCy
            dates_spacy = [
                ent.text.strip()
                for ent in doc.ents
                if ent.label_ == 'DATE'
            ]

            logging.info(f"SpaCy orgs formation: {orgs[:5]}")
            logging.info(f"SpaCy dates formation: {dates_spacy[:5]}")

            # Utiliser SpaCy pour l'école si regex n'a rien trouvé
            if not ecole and orgs:
                ecole = orgs[0]

        except Exception as e:
            logging.warning(f"SpaCy formation: {e}")

    # ✅ Chercher l'année (Regex)
    years = re.findall(r'\b(20\d{2}|19\d{2})\b', source)
    if years:
        annee = max(int(y) for y in years)

    return diplome, ecole, annee


def parse_annees_experience_nlp(text_experience, text_complet):
    """
    Calcule les années d'expérience
    SpaCy → détecter les entités DATE
    Regex → calculer depuis les plages de dates
    """
    source = text_experience if text_experience.strip() else text_complet

    # ✅ Regex — Mention directe
    mention = re.search(
        r'(\d+)\s*(?:\+\s*)?ans?\s*d[\'e]?\s*exp[eé]riences?',
        source, re.IGNORECASE
    )
    if mention:
        val = int(mention.group(1))
        if 0 < val <= 50:
            return val

    # ✅ SpaCy — Extraire les entités DATE
    dates_from_spacy = []
    if nlp and source:
        try:
            doc = nlp(source[:5000])
            for ent in doc.ents:
                if ent.label_ == 'DATE':
                    # Chercher années dans les entités DATE
                    years_found = re.findall(
                        r'\b(20\d{2}|19\d{2})\b',
                        ent.text
                    )
                    dates_from_spacy.extend(years_found)
            logging.info(f"SpaCy dates: {dates_from_spacy}")
        except Exception as e:
            logging.warning(f"SpaCy dates: {e}")

    # ✅ Regex — Calculer depuis plages de dates
    date_ranges = re.findall(
        r'(\d{4})\s*[-–—]\s*(\d{4}|présent|present|actuel|aujourd\'hui|en\s*cours)',
        source, re.IGNORECASE
    )

    annee_courante = datetime.datetime.now().year
    total_mois     = 0

    for debut_str, fin_str in date_ranges:
        debut     = int(debut_str)
        fin_lower = fin_str.lower().strip()

        if any(w in fin_lower for w in
               ['présent', 'present', 'actuel', 'aujourd', 'cours']):
            fin = annee_courante
        else:
            try:
                fin = int(fin_str)
            except ValueError:
                fin = annee_courante

        if 1970 <= debut <= annee_courante and debut <= fin:
            total_mois += (fin - debut) * 12

    if total_mois > 0:
        return min(round(total_mois / 12), 50)

    # ✅ Fallback SpaCy — si regex n'a rien trouvé
    if dates_from_spacy:
        years_int = [int(y) for y in dates_from_spacy
                     if 1970 <= int(y) <= annee_courante]
        if len(years_int) >= 2:
            annees = max(years_int) - min(years_int)
            return min(annees, 50)

    return 0

# ══════════════════════════════════════════════
# MATCHING CV / OFFRE AVEC SPACY
# ══════════════════════════════════════════════

def get_niveau(score):
    if score >= 80: return 'Excellent'
    if score >= 60: return 'Bon'
    if score >= 40: return 'Moyen'
    return 'Faible'


def calculate_score(cv_data, competences_requises, description_offre):
    score_total = 0
    details     = {}

    texte_offre = (
        (competences_requises or '') + ' ' +
        (description_offre    or '')
    ).lower().strip()

    texte_cv = (
        (cv_data.get('competencesTech',  '') or '') + ' ' +
        (cv_data.get('experienceProf',   '') or '') + ' ' +
        (cv_data.get('competencesPerso', '') or '') + ' ' +
        (cv_data.get('dernierDiplome',   '') or '') + ' ' +
        (cv_data.get('ecoleUniversite',  '') or '')
    ).lower().strip()

    # ══════════════════════════════════════
    # FONCTION HELPER — Similarité SpaCy
    # ══════════════════════════════════════
    def spacy_similarity(texte_a, texte_b):
        """
        Calcule la similarité sémantique entre deux textes
        via les vecteurs de mots SpaCy
        → Comprend les synonymes et le sens
        """
        if not nlp or not texte_a or not texte_b:
            return 0.0
        try:
            doc_a = nlp(texte_a[:2000])
            doc_b = nlp(texte_b[:2000])
            # ✅ SpaCy calcule la similarité cosinus
            # entre les vecteurs des deux textes
            sim = doc_a.similarity(doc_b)
            logging.info(f"SpaCy similarity: {sim:.3f}")
            return sim
        except Exception as e:
            logging.warning(f"SpaCy similarity: {e}")
            return 0.0

    def extract_mots(texte, min_len=2):
        """Extrait les mots significatifs"""
        mots_vides = {
            'les', 'des', 'une', 'est', 'pour', 'avec', 'dans',
            'sur', 'par', 'qui', 'que', 'aux', 'the', 'and',
            'for', 'with', 'this', 'that', 'are'
        }
        mots = set(re.findall(
            r'[a-zA-Z0-9#+.\-]{%d,}' % min_len, texte
        ))
        return {m for m in mots if m not in mots_vides}

    def score_matching_hybride(texte_a, texte_b, max_score):
        """
        Matching hybride :
        ✅ 60% SpaCy (similarité sémantique)
        ✅ 40% Regex (mots communs)
        """
        if not texte_a or not texte_b:
            return max_score // 3

        # ✅ Score SpaCy (similarité sémantique)
        if nlp:
            sim_spacy  = spacy_similarity(texte_a, texte_b)
            score_spacy = round(sim_spacy * max_score * 0.6)
        else:
            score_spacy = 0

        # ✅ Score Regex (mots communs)
        mots_a = extract_mots(texte_a)
        mots_b = extract_mots(texte_b)

        if mots_a and mots_b:
            exact   = mots_a & mots_b
            partiel = set()
            for mot_b in mots_b:
                if mot_b in exact:
                    continue
                for mot_a in mots_a:
                    if (mot_b in mot_a or mot_a in mot_b) and \
                       abs(len(mot_a) - len(mot_b)) <= 3:
                        partiel.add(mot_b)
                        break

            ratio       = (len(exact) + len(partiel) * 0.7) / len(mots_b)
            ratio       = min(ratio, 1.0)
            score_regex = round(ratio * max_score * 0.4)
        else:
            score_regex = 0

        # ✅ Bonus si CV riche
        bonus = max_score * 0.1 if len(mots_a) > 20 else 0

        score = round(score_spacy + score_regex + bonus)
        return min(score, max_score)

    # ══════════════════════════════════════
    # 1. Compétences techniques (40 points)
    # ══════════════════════════════════════
    tech_cv  = cv_data.get('competencesTech', '').lower()
    comp_req = competences_requises.lower() if competences_requises else ''

    if tech_cv and comp_req:
        score_tech = score_matching_hybride(tech_cv, comp_req, 40)
    elif tech_cv and texte_offre:
        score_tech = score_matching_hybride(tech_cv, texte_offre, 40)
        score_tech = round(score_tech * 0.8)
    elif tech_cv:
        score_tech = 20
    else:
        score_tech = 0

    details['competences_techniques'] = score_tech
    score_total += score_tech

    # ══════════════════════════════════════
    # 2. Expérience (25 points)
    # ══════════════════════════════════════
    xp_cv      = cv_data.get('experienceProf', '').lower()
    desc_offre = description_offre.lower() if description_offre else ''

    if xp_cv and desc_offre:
        score_xp = score_matching_hybride(xp_cv, desc_offre, 25)
    elif xp_cv and texte_offre:
        score_xp = score_matching_hybride(xp_cv, texte_offre, 25)
        score_xp = round(score_xp * 0.8)
    elif xp_cv:
        score_xp = 12
    else:
        score_xp = 0

    details['experience'] = score_xp
    score_total += score_xp

    # ══════════════════════════════════════
    # 3. Années expérience (15 points)
    # ══════════════════════════════════════
    annees_cv        = cv_data.get('anneesExperience', 0) or 0
    annees_req_match = re.search(
        r'(\d+)\s*(?:\+\s*)?ans?\s*d[\'e]?\s*exp[eé]riences?',
        texte_offre, re.IGNORECASE
    )

    if annees_req_match:
        annees_req = int(annees_req_match.group(1))
        if annees_cv >= annees_req:         score_annees = 15
        elif annees_cv >= annees_req * 0.7: score_annees = 10
        elif annees_cv >= annees_req * 0.5: score_annees = 7
        elif annees_cv > 0:                 score_annees = 3
        else:                               score_annees = 0
    else:
        if annees_cv >= 5:   score_annees = 15
        elif annees_cv >= 3: score_annees = 12
        elif annees_cv >= 1: score_annees = 8
        else:                score_annees = 5

    details['annees_experience'] = score_annees
    score_total += score_annees

    # ══════════════════════════════════════
    # 4. Formation / Diplôme (10 points)
    # ══════════════════════════════════════
    diplome_cv = cv_data.get('dernierDiplome', '').lower()

    niveaux = {
        'doctorat' : 5, 'phd'     : 5,
        'master'   : 4, 'mastère' : 4, 'bac+5' : 4,
        'licence'  : 3, 'bac+3'   : 3, 'bac+4' : 3,
        'bts'      : 2, 'dut'     : 2, 'bac+2' : 2,
        'bac'      : 1
    }

    niveau_cv  = 0
    niveau_req = 0

    for niveau, val in niveaux.items():
        if niveau in diplome_cv:
            niveau_cv = val
            break

    for niveau, val in niveaux.items():
        if niveau in texte_offre:
            niveau_req = val
            break

    # ✅ SpaCy — Similarité diplôme vs offre
    if nlp and diplome_cv and texte_offre:
        sim_diplome = spacy_similarity(diplome_cv, texte_offre)
        bonus_diplome = round(sim_diplome * 3)
    else:
        bonus_diplome = 0

    if niveau_req == 0:
        score_diplome = min(niveau_cv * 2, 10) if niveau_cv > 0 else 5
    elif niveau_cv >= niveau_req:
        score_diplome = 10
    elif niveau_cv == niveau_req - 1:
        score_diplome = 7
    elif niveau_cv > 0:
        score_diplome = 4
    else:
        score_diplome = 2

    score_diplome = min(score_diplome + bonus_diplome, 10)
    details['formation'] = score_diplome
    score_total += score_diplome

    # ══════════════════════════════════════
    # 5. Soft skills (10 points)
    # ══════════════════════════════════════
    soft_cv = cv_data.get('competencesPerso', '').lower()

    if soft_cv and texte_offre:
        score_soft = score_matching_hybride(soft_cv, texte_offre, 10)
        if len(soft_cv) > 50:
            score_soft = max(score_soft, 5)
    elif soft_cv:
        score_soft = 5
    else:
        score_soft = 0

    details['soft_skills'] = score_soft
    score_total += score_soft

    # ══════════════════════════════════════
    # Score final
    # ══════════════════════════════════════
    score_final = min(score_total, 100)

    logging.info(f"Détails : {details}")
    logging.info(f"Score final : {score_final}")

    return {
        'score'  : score_final,
        'niveau' : get_niveau(score_final),
        'details': details
    }

# ══════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════

@app.route('/parse-cv', methods=['POST'])
def parse_cv():
    try:
        data = request.get_json(force=True, silent=True)

        if not data:
            return jsonify({'error': 'Body JSON requis'}), 400

        file_base64  = data.get('fileBase64', '')
        file_name    = data.get('fileName', '').lower().strip()
        content_type = data.get('contentType', '').lower().strip()

        if not file_base64:
            return jsonify({'error': 'fileBase64 manquant'}), 400

        if ',' in file_base64:
            file_base64 = file_base64.split(',')[1]

        file_base64 = (file_base64.strip()
                       .replace(' ', '+')
                       .replace('\n', '')
                       .replace('\r', '')
                       .replace('\t', ''))

        missing_padding = len(file_base64) % 4
        if missing_padding:
            file_base64 += '=' * (4 - missing_padding)

        try:
            file_bytes = base64.b64decode(file_base64, validate=False)
        except Exception as e:
            return jsonify({'error': f'Base64 invalide: {str(e)}'}), 400

        is_pdf  = 'pdf' in content_type or file_name.endswith('.pdf')
        is_docx = ('officedocument' in content_type or
                   file_name.endswith('.docx'))
        is_doc  = 'msword' in content_type or file_name.endswith('.doc')

        if not is_pdf and not is_docx and not is_doc:
            if file_bytes[:4] == b'%PDF':
                is_pdf = True
            elif file_bytes[:2] == b'PK':
                is_docx = True

        text = ""
        if is_pdf:
            text = extract_text_from_pdf(file_bytes)
        elif is_docx:
            text = extract_text_from_docx(file_bytes)
        elif is_doc:
            text = extract_text_from_doc(file_bytes)
        else:
            return jsonify({'error': 'Format non supporté'}), 400

        if not text.strip():
            return jsonify({
                'anneesExperience': 0,
                'competencesTech' : '',
                'experienceProf'  : '',
                'competencesPerso': '',
                'dernierDiplome'  : '',
                'ecoleUniversite' : '',
                'anneeObtention'  : None,
                'warning'         : 'Impossible d\'extraire le texte.'
            }), 200

        sections = split_sections(text)

        diplome, ecole, annee = parse_formation_nlp(
            sections['formation'], text
        )

        result = {
            'anneesExperience': parse_annees_experience_nlp(
                sections['experience'], text),
            'competencesTech' : parse_competences_tech_nlp(
                sections['competences'], text),
            'experienceProf'  : parse_experience_prof_nlp(
                sections['experience'], text),
            'competencesPerso': parse_competences_perso_nlp(
                sections['profil'], text),
            'dernierDiplome'  : diplome,
            'ecoleUniversite' : ecole,
            'anneeObtention'  : annee,
        }

        return jsonify(result), 200

    except Exception as e:
        logging.error(f"Erreur: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/match-cv', methods=['POST'])
def match_cv():
    try:
        data = request.get_json(force=True, silent=True)

        if not data:
            return jsonify({'error': 'Body JSON requis'}), 400

        cv_data              = data.get('cvData', {})
        competences_requises = data.get('competencesRequises', '')
        description_offre    = data.get('descriptionOffre', '')

        if not cv_data:
            return jsonify({'error': 'cvData manquant'}), 400

        result = calculate_score(
            cv_data,
            competences_requises,
            description_offre
        )

        return jsonify({
            'score'  : result['score'],
            'niveau' : result['niveau'],
            'details': result['details']
        }), 200

    except Exception as e:
        logging.error(f"Erreur match-cv: {e}", exc_info=True)
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