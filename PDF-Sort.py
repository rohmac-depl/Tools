import streamlit as st
import fitz  # PyMuPDF
import re
import io
import zipfile
import qrcode
from datetime import datetime

# --- LOGIK TEIL ---

def generate_qr_code(content):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=1,
    )
    qr.add_data(content)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    return img_byte_arr.getvalue()

def get_sort_key(text):
    match = re.search(r"(?:Reference No\.|Referenznr\.|Ref\.|Art\.)\s*:\s*(.*)", text, re.IGNORECASE)
    if match:
        ref_str = match.group(1)
        first_item = ref_str.split(',')[0]
        
        qty = 1
        qty_match = re.search(r"/(\d+)", first_item)
        if qty_match:
            qty = int(qty_match.group(1))
            
        dim_match = re.search(r"(\d+)\s*[xX]\s*(\d+)", first_item)
        if dim_match:
            width = int(dim_match.group(1))
            height = int(dim_match.group(2))
            return width, height, qty
        
        single_match = re.search(r"(\d+)", first_item)
        if single_match:
            return int(single_match.group(1)), 0, qty
            
    return 99999, 99999, 99999

def clean_and_format_item(raw_string):
    try:
        if "/" in raw_string:
            parts = raw_string.split('/')
            produkt_teil = parts[0].strip()
            menge_str = parts[1].strip()
        else:
            produkt_teil = raw_string.strip()
            menge_str = "1"

        match_prod = re.search(r"(BSM|TSM|KND).*", produkt_teil, re.IGNORECASE)
        if match_prod:
            clean_produkt = match_prod.group(0)
        elif "-" in produkt_teil:
            try:
                clean_produkt = produkt_teil.split("-", 1)[1]
            except:
                clean_produkt = produkt_teil
        else:
            clean_produkt = produkt_teil
        
        clean_produkt = clean_produkt.upper()
        clean_produkt = re.sub(r"(\d)\s*X\s*(\d)", r"\1x\2", clean_produkt)
        
        # QR Code Logik
        qr_content = None
        if "TSM" in clean_produkt:
            dim_search = re.search(r"(\d+)x(\d+)", clean_produkt)
            if dim_search:
                w_cm = int(dim_search.group(1))
                h_cm = int(dim_search.group(2))
                qr_content = f"re{w_cm*10}x{h_cm*10}"
        
        qty_text = None
        try:
            if int(menge_str) > 1:
                qty_text = f"{menge_str} Stück"
        except:
            pass
            
        return clean_produkt, qty_text, qr_content
    except:
        return raw_string, None, None

# --- HIER IST DIE KORRIGIERTE FUNKTION MIT SICHERHEITEN ---
def get_optimal_fontsize(text, fontname, max_width, max_fontsize):
    """
    Berechnet die maximale Schriftgröße, damit der Text in die Breite passt.
    Sicherungen hinzugefügt, um String/Int-Fehler zu vermeiden.
    """
    try:
        # 1. Eingabewerte absichern und in int konvertieren
        current_fontsize = int(max_fontsize)
        available_width = int(max_width)
    except ValueError:
        # Falls max_width oder max_fontsize kein valider Integer ist, Standardwert zurückgeben
        return 12 
    
    # Berechne die Länge des Textes bei der maximalen Schriftgröße
    text_length = fitz.get_text_length(text, fontname=fontname, fontsize=current_fontsize)
    
    # Wenn der Text breiter ist als erlaubt
    if text_length > available_width:
        # Berechne Skalierungsfaktor (Verhältnis von Platz zu Textlänge)
        factor = available_width / text_length
        # Wende Faktor an und ziehe sicherheitshalber 5% ab (Padding)
        current_fontsize = int(current_fontsize * factor * 0.95)
        
        # Minimale Schriftgröße, damit es nicht unlesbar wird
        if current_fontsize < 10:
            current_fontsize = 10
            
    return current_fontsize
# ----------------------------------------------------------

def process_single_pdf(input_file_bytes):
    doc = fitz.open(stream=input_file_bytes, filetype="pdf")
    total_pages = len(doc)
    
    # 1. SORTIERUNG
    page_meta = []
    for i in range(len(doc)):
        page = doc[i]
        text = page.get_text("text")
        sort_key = get_sort_key(text)
        page_meta.append({'index': i, 'sort_key': sort_key})

    page_meta.sort(key=lambda x: x['sort_key'])
    sorted_indices = [item['index'] for item in page_meta]
    doc.select(sorted_indices)

    # 2. BEARBEITUNG
    modified_count = 0
    current_date = datetime.now().strftime("%d/%m/%Y")

    for page_num, page in enumerate(doc):
        text = page.get_text("text")
        
        match = re.search(r"(?:Reference No\.|Referenznr\.|Ref\.|Art\.)\s*:\s*(.*)", text, re.IGNORECASE)
        
        if match:
            original_ref = match.group(1).strip()
            raw_items = original_ref.split(',')
            
            if len(raw_items) == 1:
                # --- A: EINZELNER ARTIKEL ---
                prod_name, qty_text, qr_string = clean_and_format_item(raw_items[0])
                
                rect_y_main = page.rect.height - 350
                # Definiere den Bereich für den Text
                text_rect_main = fitz.Rect(0, rect_y_main, page.rect.width, rect_y_main + 100)
                
                # Dynamische Schriftgröße berechnen
                available_width = page.rect.width - 20 
                optimal_size = get_optimal_fontsize(prod_name, "helv", available_width, 26)
                
                page.insert_textbox(text_rect_main, prod_name, fontsize=optimal_size, fontname="helv", align=1)
                
                if qty_text:
                    rect_y_sub = page.rect.height - 320
                    text_rect_sub = fitz.Rect(0, rect_y_sub, page.rect.width, rect_y_sub + 60)
                    page.insert_textbox(text_rect_sub, qty_text, fontsize=16, fontname="helv", align=1)
                
                if qr_string:
                    qr_bytes = generate_qr_code(qr_string)
                    qr_size = 45
                    qr_x_start = 10
                    qr_y_start = rect_y_main + 5
                    qr_rect = fitz.Rect(qr_x_start, qr_y_start, qr_x_start + qr_size, qr_y_start + qr_size)
                    page.insert_image(qr_rect, stream=qr_bytes)

            else:
                # --- B: MEHRERE ARTIKEL ---
                current_y = page.rect.height - 355 
                line_height = 35 
                available_width = page.rect.width - 60 
                
                for item_raw in raw_items:
                    prod_name, qty_text, qr_string = clean_and_format_item(item_raw)
                    
                    r_name = fitz.Rect(0, current_y, page.rect.width, current_y + 40)
                    
                    # Auch hier dynamische Anpassung
                    optimal_size_list = get_optimal_fontsize(prod_name, "helv", available_width, 18)
                    
                    page.insert_textbox(r_name, prod_name, fontsize=optimal_size_list, fontname="helv", align=1)
                    
                    if qty_text:
                        r_qty = fitz.Rect(0, current_y + 16, page.rect.width, current_y + 50)
                        page.insert_textbox(r_qty, qty_text, fontsize=12, fontname="helv", align=1)
                        
                    if qr_string:
                        qr_bytes = generate_qr_code(qr_string)
                        qr_list_size = 40
                        qr_x_start = 10
                        qr_rect = fitz.Rect(qr_x_start, current_y, qr_x_start + qr_list_size, current_y + qr_list_size)
                        page.insert_image(qr_rect, stream=qr_bytes)

                    current_y += line_height + (12 if qty_text else 0)
            
            modified_count += 1

        # --- C. DATUM (-280) ---
        box_y = page.rect.height - 280
        width_third = page.rect.width / 3
        date_rect = fitz.Rect(width_third, box_y, width_third * 2, box_y + 40)
        
        page.insert_textbox(
            date_rect,
            current_date,
            fontsize=10,        
            fontname="helv",
            align=1             
        )

    output_buffer = io.BytesIO()
    doc.save(output_buffer)
    output_buffer.seek(0)
    return output_buffer, modified_count, total_pages

# --- FRONTEND DESIGN ---

st.set_page_config(page_title="ROHMAC PDF Sortierer", page_icon="📑", layout="centered")

st.markdown("""
    <style>
    .stApp {background-color: #f4f7f6;}
    .main .block-container {
        max-width: 750px;
        padding-top: 2rem;
        padding-bottom: 3rem;
        background-color: white;
        border-radius: 15px;
        border-top: 8px solid #0056b3;
        box-shadow: 0 4px 20px rgba(0,0,0,0.08);
        margin-top: 2rem;
    }
    h1 {
        color: #0056b3; font-weight: 800; text-align: center; 
        font-size: 2.5rem !important; margin-bottom: 0.5rem !important; 
        text-transform: uppercase; letter-spacing: 1px;
    }
    p {color: #555; font-size: 1.1rem; text-align: center;}
    .stFileUploader {padding: 1rem 0;}
    
    .stButton > button {
        width: 100%;
        background: linear-gradient(90deg, #0056b3 0%, #004494 100%);
        color: #ffffff !important;
        border: none;
        padding: 1rem 2rem;
        font-size: 1.3rem;
        border-radius: 10px;
        font-weight: 600;
        box-shadow: 0 4px 6px rgba(0, 86, 179, 0.3);
        transition: all 0.2s;
        margin-top: 10px;
    }
    .stButton > button * {color: #ffffff !important;}
    .stButton > button:hover {
        transform: translateY(-2px); 
        box-shadow: 0 6px 12px rgba(0, 86, 179, 0.4); 
        color: #ffffff !important;
    }
    .stButton > button:active {transform: translateY(0px);}
    
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)

# --- APP INHALT ---

st.title("ROHMAC PDF Sortierer")
st.write("Automatische Sortierung • Layout Optimierung • TSM Scan-Code (mm)")

st.markdown("---")

uploaded_files = st.file_uploader("Legen Sie Ihre DHL PDF-Dateien hier ab", type="pdf", accept_multiple_files=True)

if uploaded_files:
    
    btn_text = "Datei verarbeiten" if len(uploaded_files) == 1 else f"{len(uploaded_files)} Dateien verarbeiten"
    start_btn = st.button(btn_text)

    if start_btn:
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        try:
            if len(uploaded_files) == 1:
                single_file = uploaded_files[0]
                status_text.text(f"Verarbeite: {single_file.name}")
                pdf_bytes, count, total = process_single_pdf(single_file.getvalue())
                progress_bar.progress(100)
                
                if count > 0:
                    st.success(f"Fertig! {count} von {total} Etiketten verarbeitet.")
                    with st.expander("Vorschau anzeigen", expanded=True):
                        doc_preview = fitz.open(stream=pdf_bytes.getvalue(), filetype="pdf")
                        page_preview = doc_preview[0]
                        pix = page_preview.get_pixmap(dpi=100)
                        # Korrektur 1: use_container_width=True ersetzt durch width="stretch"
                        st.image(pix.tobytes(), width="stretch")
                    
                    st.markdown("### Download")
                    st.download_button(
                        label="📥 Fertige PDF herunterladen",
                        data=pdf_bytes,
                        file_name=f"Bearbeitet_{single_file.name}",
                        mime="application/pdf"
                    )
                else:
                    st.warning("Keine passenden Etiketten gefunden.")

            else:
                zip_buffer = io.BytesIO()
                total_processed_files = 0
                total_labels = 0
                preview_image_bytes = None

                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                    for idx, uploaded_file in enumerate(uploaded_files):
                        status_text.text(f"Verarbeite Datei {idx+1} von {len(uploaded_files)}: {uploaded_file.name}")
                        file_bytes = uploaded_file.getvalue()
                        pdf_bytes, count, pages = process_single_pdf(file_bytes)
                        
                        if count > 0:
                            zf.writestr(f"Sortiert_{uploaded_file.name}", pdf_bytes.getvalue())
                            total_processed_files += 1
                            total_labels += pages
                            if preview_image_bytes is None:
                                doc_preview = fitz.open(stream=pdf_bytes.getvalue(), filetype="pdf")
                                pix = doc_preview[0].get_pixmap(dpi=100)
                                preview_image_bytes = pix.tobytes()
                        
                        progress_bar.progress((idx + 1) / len(uploaded_files))

                progress_bar.empty()
                status_text.empty()
                
                if total_processed_files > 0:
                    st.success(f"Fertig! {total_processed_files} Dateien verarbeitet.")
                    if preview_image_bytes:
                        with st.expander("Beispiel-Vorschau", expanded=True):
                            # Korrektur 2: use_container_width=True ersetzt durch width="stretch"
                            st.image(preview_image_bytes, width="stretch")

                    zip_buffer.seek(0)
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    
                    st.markdown("### Download")
                    st.download_button(
                        label="📥 Alle Dateien als ZIP herunterladen",
                        data=zip_buffer,
                        file_name=f"ROHMAC_Etiketten_{today_str}.zip",
                        mime="application/zip"
                    )
                else:
                    st.warning("Keine passenden Daten gefunden.")

        except Exception as e:
            st.error(f"Ein Fehler ist aufgetreten: {e}")

else:
    st.info("Bitte laden Sie eine oder mehrere PDF-Dateien hoch.")