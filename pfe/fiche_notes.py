"""
Fiche de notes de l'enseignant (Excel) pour une matière, prête à remplir, à imprimer, à signer et à importer.

- Colonnes de notes selon le règlement des inscrits : arrêté 877 (Tronc commun et cycle Ingénieur depuis
  2025-2026) → CC, TP, CT, Moyenne, Rattrapage ; autres cycles (en attendant leurs propres règles) → Contrôle,
  TP, Examen, Moyenne, Rattrapage. L'import des notes lit les deux (CC = controle, CT = examen). La moyenne
  ne se remplit que si l'enseignant la donne directement (sinon gePed la calcule).
- Les colonnes Matière et Session sont pré-remplies : le fichier rempli s'importe tel quel.
- Au-dessus du tableau : établissement, programme, licence, semestre, session, matière, enseignant ;
  en dessous : le CODE DE CONTRÔLE puis la zone de signature (l'import ignore ces lignes).
- Code de contrôle : calculé par une formule Excel à partir de toutes les notes, imprimé sur la fiche signée.
  À l'import, gePed le recalcule depuis le fichier : s'il diffère du code lu sur la fiche signée (une note
  changée après la signature), l'import est annulé. Le calcul Python (`valeur_controle`, `code_fiche`)
  reproduit exactement la formule Excel.
- Une colonne cachée « Clé gePed » (M) repère la fiche à l'import et porte le calcul de chaque ligne.
"""
import io
import re
import zlib
from decimal import Decimal, ROUND_HALF_UP

MARQUEUR = 'Clé gePed'            # en-tête de la colonne cachée (normalisé : « cle_geped »)
COLONNE_MARQUEUR = 'cle_geped'
DIVISEUR = 999983
CHAMPS_FICHE = ('controle', 'tp', 'examen', 'moyenne', 'rattrapage')       # colonnes H à L de la fiche


def libelles_fiche(code=''):
    """Intitulés des colonnes de notes selon le règlement."""
    from .notes import regles
    if code and regles(code).get('calcul') == 'cc_ct':
        return ['CC', 'TP', 'CT', 'Moyenne', 'Rattrapage']
    return ['Contrôle', 'TP', 'Examen', 'Moyenne', 'Rattrapage']


def poids_matricule(matricule):
    """Poids d'une ligne, tiré du matricule : le code ne dépend pas de l'ordre des lignes."""
    return zlib.crc32(str(matricule or '').strip().upper().encode()) % 9973 + 1


def valeur_controle(v):
    """
    Même calcul que la formule Excel de la fiche, case par case :
    nombre → ROUND(note × 100) + 1 ; « ABS » → 1001 ; vide → 0 ; autre texte → 5003.
    """
    if v is None or v == '':
        return 0
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int((Decimal(repr(v)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)) + 1
    return 1001 if str(v).strip().upper() == 'ABS' else 5003


def total_ligne(matricule, valeurs):
    """`valeurs` : notes brutes de la ligne dans l'ordre contrôle (CC), TP, examen (CT), moyenne, rattrapage."""
    return poids_matricule(matricule) * sum(valeur_controle(v) * (j + 1) for j, v in enumerate(valeurs))


def code_fiche(total):
    return str(total % DIVISEUR).zfill(6)


def nettoyer_code(saisi):
    return re.sub(r'\D', '', str(saisi or ''))


def _formule_case(ref):
    return f'IF(ISNUMBER({ref}),ROUND({ref}*100,0)+1,IF(UPPER(TRIM({ref}))="ABS",1001,IF({ref}="",0,5003)))'


def code_majoritaire(inscriptions):
    """Règlement de la plupart des inscrits (une matière n'en a normalement qu'un)."""
    from collections import Counter
    from .notes import codes_inscriptions
    codes = Counter(codes_inscriptions(inscriptions).values())
    return codes.most_common(1)[0][0] if codes else ''


def fiche_notes_xlsx(matiere, inscriptions):
    """Classeur de la fiche ; `inscriptions` : les inscrits de la matière (avec étudiant et session)."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
    from openpyxl.worksheet.datavalidation import DataValidation
    from .notes import regles

    code = code_majoritaire(inscriptions)
    r = regles(code)
    libelles = libelles_fiche(code)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Notes'

    bleu, encre = '41627C', '1F2D3D'
    fin = Side(style='thin', color='9CA3AF')
    cadre = Border(left=fin, right=fin, top=fin, bottom=fin)
    gras = Font(bold=True, color=encre)

    # ---- En-tête (ignoré par l'import)
    ws.merge_cells('A1:L1')
    ws['A1'] = 'INSTITUT SUPÉRIEUR DE TECHNOLOGIE DE MAMOU'
    ws['A1'].font = Font(bold=True, size=14, color=bleu)
    ws.merge_cells('A2:L2')
    ws['A2'] = "FICHE DE NOTES DE L'ENSEIGNANT"
    ws['A2'].font = Font(bold=True, size=12, color=encre)
    for cellule in ('A1', 'A2'):
        ws[cellule].alignment = Alignment(horizontal='center')
    infos = [
        ('Programme', str(matiere.filiere or ''), 'Session', matiere.session.annee),
        ('Licence', matiere.licence.nom, 'Semestre', matiere.semestre.nom),
        ('Matière', f"{matiere.nom}{f' ({matiere.code})' if matiere.code else ''}", 'Crédits', matiere.credit),
        ('Enseignant', '', 'Téléphone', ''),
    ]
    for k, (l1, v1, l2, v2) in enumerate(infos, start=4):
        ws.merge_cells(start_row=k, start_column=1, end_row=k, end_column=2)
        ws.cell(row=k, column=1, value=f'{l1} :').font = gras
        ws.merge_cells(start_row=k, start_column=3, end_row=k, end_column=6)
        ws.cell(row=k, column=3, value=v1)
        ws.cell(row=k, column=7, value=f'{l2} :').font = gras
        ws.merge_cells(start_row=k, start_column=8, end_row=k, end_column=12)
        ws.cell(row=k, column=8, value=v2).alignment = Alignment(horizontal='left')
    if r.get('calcul') == 'cc_ct':
        regle = (f"{r.get('nom', 'Arrêté 877')} : moyenne = CC 40 % + CT 60 % (le TP fait partie du CC) ; "
                 f"rattrapage retenu plafonné à {r.get('plafond_rattrapage') or 5}. Moyenne : seulement si vous la donnez "
                 f"directement. Notes sur 10, ABS = absent.")
    else:
        regle = ("Moyenne = contrôle 25 % + TP 35 % + examen 40 % (sans TP : 40 % + 60 %) ; le rattrapage remplace "
                 "la moyenne s'il est meilleur. Moyenne : seulement si vous la donnez directement. Notes sur 10, "
                 "ABS = absent.")
    ws.merge_cells('A8:L8')
    ws['A8'] = regle
    ws['A8'].font = Font(italic=True, size=9, color='4B5563')

    # ---- Tableau (lu par l'import : la ligne d'en-têtes contient « Matricule »)
    entete = 10
    titres = ['N°', 'Matricule', 'Prénom', 'Nom', 'Téléphone', 'Matière', 'Session'] + libelles + [MARQUEUR]
    for j, titre in enumerate(titres, start=1):
        c = ws.cell(row=entete, column=j, value=titre)
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor=bleu)
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        c.border = cadre
    a_remplir = PatternFill('solid', fgColor='FFF7DB')
    premiere = entete + 1
    for k, insc in enumerate(inscriptions, start=0):
        ligne = premiere + k
        e = insc.etudiant
        for j, v in enumerate([k + 1, e.matricule, e.prenom, e.nom, e.telephone or '', matiere.nom,
                               matiere.session.annee], start=1):
            c = ws.cell(row=ligne, column=j, value=v)
            c.border = cadre
        for j in range(8, 13):                                   # notes (H à L) : seules cases modifiables
            c = ws.cell(row=ligne, column=j)
            c.border, c.fill = cadre, a_remplir
            c.protection = Protection(locked=False)
            c.alignment = Alignment(horizontal='center')
        refs = [f'{col}{ligne}' for col in 'HIJKL']
        somme = '+'.join(f'{_formule_case(ref)}*{j}' for j, ref in enumerate(refs, start=1))
        ws.cell(row=ligne, column=13, value=f'=({somme})*{poids_matricule(e.matricule)}')
    derniere = premiere + max(len(inscriptions), 1) - 1

    # Saisie limitée à une note de 0 à 10 ou ABS
    if inscriptions:
        dv = DataValidation(type='custom', allow_blank=True, showErrorMessage=True,
                            formula1='OR(H{0}="",AND(ISNUMBER(H{0}),H{0}>=0,H{0}<=10),UPPER(TRIM(H{0}))="ABS")'.format(premiere),
                            errorTitle='Note invalide', error='Saisissez une note de 0 à 10 (virgule décimale), ou ABS.')
        ws.add_data_validation(dv)
        dv.add(f'H{premiere}:L{derniere}')

    # ---- Code de contrôle et signatures (ignorés par l'import : ni matricule ni note)
    ligne_code = derniere + 2
    ws.merge_cells(start_row=ligne_code, start_column=2, end_row=ligne_code, end_column=3)
    ws.cell(row=ligne_code, column=2, value='Code de contrôle :').font = Font(bold=True, size=12, color=encre)
    ws.merge_cells(start_row=ligne_code, start_column=4, end_row=ligne_code, end_column=5)
    c = ws.cell(row=ligne_code, column=4, value=f'=TEXT(MOD(SUM(M{premiere}:M{derniere}),{DIVISEUR}),"000000")')
    c.font = Font(bold=True, size=14, color=bleu)
    c.alignment = Alignment(horizontal='center')
    c.border = Border(left=Side(style='medium', color=bleu), right=Side(style='medium', color=bleu),
                      top=Side(style='medium', color=bleu), bottom=Side(style='medium', color=bleu))
    ws.merge_cells(start_row=ligne_code + 1, start_column=2, end_row=ligne_code + 1, end_column=7)
    ws.cell(row=ligne_code + 1, column=2,
            value="Ce code change dès qu'une note change : il est demandé à l'import, qui est annulé si le fichier "
                  "ne correspond plus à la fiche signée.").font = Font(italic=True, size=9, color='4B5563')
    ligne_sig = ligne_code + 3
    ws.cell(row=ligne_sig, column=2, value="Date et signature de l'enseignant").font = gras
    ws.cell(row=ligne_sig, column=7, value='Visa du directeur de programme').font = gras

    # ---- Mise en page
    for col, largeur in zip('ABCDEFGHIJKLM', [5, 14, 22, 18, 13, 22, 12, 10, 8, 10, 10, 11, 10]):
        ws.column_dimensions[col].width = largeur
    ws.column_dimensions['M'].hidden = True
    ws.row_dimensions[entete].height = 30
    ws.freeze_panes = f'A{premiere}'
    ws.print_title_rows = f'{entete}:{entete}'
    ws.print_area = f'A1:L{ligne_sig + 3}'
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.oddFooter.center.text = 'Page &P / &N'
    ws.protection.sheet = True                                 # seules les notes se modifient
    ws.protection.formatCells = False
    flux = io.BytesIO()
    wb.save(flux)
    return flux.getvalue()
