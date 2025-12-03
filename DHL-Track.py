import streamlit as st
import http.client
import urllib.parse
import re
import io
import pandas as pd
import time
import xml.etree.ElementTree as ET
from pypdf import PdfReader 

# =======================================================
# 1. KONFIGURATION
# =======================================================

DHL_API_HOST = "api-eu.dhl.com"
DHL_BASIC_AUTH = "Basic Njl1NlRIb3FKd3RYNUg0Nnc2NWRWaFcwV3pIZGtaOHk6N2VLWVZhNGw3MTFsR2VGNQ=="

APP_NAME = "henrik007"
APP_PASSWORD = "4!#l#8fzBCGL6Y1"
LANGUAGE_CODE = "de" 

TRACKING_REGEX = r'(\d{20}|[A-Z]{2}\d{9}[A-Z]{2})' 

# Status-Kategorisierung (DIESE LISTE MUSS HIER BLEIBEN, WIRD IN FUNKTIONEN BENÖTIGT)
CATEGORY_MAPPING = {
    "ZU": "Zugestellt", "DLVRD": "Zugestellt", "delivered": "Zugestellt", 
    "VA": "Unterwegs", "AA": "Unterwegs", "LA": "Unterwegs", "BZ": "Unterwegs", "PO": "Unterwegs",
    "EE": "Unterwegs", "UNTR": "Unterwegs", "DD": "Unterwegs", "NB": "Unterwegs",
    "RM": "Problem", "ZN": "Problem", "HTTP 429": "Problem", "HTTP 404": "Problem", "HTTP 401": "Problem", "PARSE ERROR": "Problem", "ERROR": "Problem"
}

# Status-Übersetzung
STATUS_MAPPING = {
    "VA": "📝 Angekündigt / Vorbereitung", "AA": "📨 Auftragsannahme", "LA": "🚚 Ladeauftrag / Unterwegs", 
    "ZU": "✅ Erfolgreich zugestellt", "PO": "📍 Filiale/Packstation", "ZN": "⚠️ Nicht angetroffen", "RM": "🔄 Retoure", 
    "BZ": "🚛 In Zustellung", "delivered": "✅ Zugestellt", "EE": "🌏 Export/Verarbeitung", "NB": "(NB) In Bearbeitung",
    "DD": "(DD) Wunschort/Nachbar", "HTTP 429": "⛔ Tageslimit erreicht", "HTTP 404": "❌ Nicht gefunden", "HTTP 401": "🔒 Zugriff fehlgeschlagen" 
}

# Session State initialisieren
if 'filter_status' not in st.session_state:
    st.session_state.filter_status = 'Gesamt'
if 'data_frame' not in st.session_state:
    st.session_state.data_frame = pd.DataFrame()
if 'needs_reset' not in st.session_state:
    st.session_state.needs_reset = False 


# =======================================================
# 2. FUNKTIONEN
# =======================================================

def uebersetze_status(code):
    return STATUS_MAPPING.get(code, code)

def get_category(status_code, info_text):
    """Ordnet den Status einer Kategorie zu."""
    if status_code in CATEGORY_MAPPING:
        return CATEGORY_MAPPING[status_code]
    
    if "FEHLER" in info_text or "401" in info_text or "404" in info_text or "ERROR" in info_text:
        return "Problem"
    
    return "Problem" 

# --- HILFSFUNKTIONEN (XML, PARSING etc.) ---
def extrahiere_sendungsnummern(pdf_file, regex):
    sendungsnummern = set()
    try:
        reader = PdfReader(pdf_file)
        for page in reader.pages:
            text = page.extract_text().replace(" ", "").replace("\n", "")
            gefundene_nummern = re.findall(regex, text)
            
            for num in gefundene_nummern:
                if isinstance(num, tuple):
                    non_empty_num = next((item for item in num if item), None)
                    if non_empty_num:
                        sendungsnummern.add(non_empty_num)
                else:
                    sendungsnummern.add(num)
    except Exception as e:
        st.error(f"Fehler beim Lesen der PDF: {e}")
    return list(sendungsnummern)

def get_tracking_xml(piece_code: str) -> str:
    xml_data = (f'<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
        f'<data appname="{APP_NAME}" language-code="{LANGUAGE_CODE}" '
        f'password="{APP_PASSWORD}" piece-code="{piece_code}" '
        f'request="d-get-piece-detail" />')
    return urllib.parse.quote(xml_data, safe='')

def parse_xml_response(xml_string):
    try:
        if xml_string.startswith("<?xml"):
            xml_string = xml_string[xml_string.find('?>') + 2:].strip()
            
        root = ET.fromstring(xml_string)
        shipment_data = root.find(".//data[@name='piece-shipment']")
        
        if shipment_data is not None:
            status_text = shipment_data.get('status', 'Unbekannt')
            short_status = shipment_data.get('short-status', '')
            timestamp = shipment_data.get('status-timestamp', '')
            ice_code = shipment_data.get('standard-event-code') or shipment_data.get('ice', 'UNKN')
            recipient = shipment_data.get('recipient-city', '')
            
            return {
                "Status Code": ice_code,
                "Info (Klartext)": f"({ice_code}) {short_status or status_text}",
                "Zeitpunkt": timestamp,
                "Ort": recipient
            }
        else:
            return {"Status Code": "404", "Info (Klartext)": "Keine Daten gefunden", "Zeitpunkt": "", "Ort": ""}
            
    except ET.ParseError:
        return {"Status Code": "PARSE ERROR", "Info (Klartext)": "Antwort-Fehler", "Zeitpunkt": "", "Ort": ""}

def track_shipment_live(tracking_number):
    conn = http.client.HTTPSConnection(DHL_API_HOST)
    encoded_xml = get_tracking_xml(tracking_number)

    headers = {'Authorization': DHL_BASIC_AUTH}
    endpoint = f"/parcel/de/tracking/v0/shipments?xml={encoded_xml}"

    try:
        conn.request("GET", endpoint, "", headers)
        response = conn.getresponse()
        data = response.read()
        xml_response = data.decode("utf-8")
        
        if response.status != 200:
             return {"Status Code": f"HTTP {response.status}", "Info (Klartext)": f"API Fehler {response.status} ({response.reason})", "Zeitpunkt": "", "Ort": ""}
        
        return parse_xml_response(xml_response)
        
    except Exception as e:
        return {"Status Code": "CONN ERROR", "Info (Klartext)": str(e), "Zeitpunkt": "", "Ort": ""}
    finally:
        conn.close()

@st.cache_data(show_spinner=False)
def process_batch(nummern_liste, _time_stamp_identifier, _progress_bar, _metrics_placeholder, _table_placeholder):
    """
    Verarbeitet die Liste und speichert die Ergebnisse im Cache (die Platzhalter werden ignoriert).
    """
    results = []
    
    progress_bar = _progress_bar or st.empty()
    metrics_placeholder = _metrics_placeholder or st.empty()
    table_placeholder = _table_placeholder or st.empty()
    
    count_delivered = 0
    count_transit = 0
    count_exception = 0
    
    for i, num in enumerate(nummern_liste):
        
        progress_bar.progress((i + 1) / len(nummern_liste), text=f"Prüfe {i+1}/{len(nummern_liste)}: {num}")
        
        data = track_shipment_live(num)
        
        # 1. Kategorisierung und Zuweisung
        category = get_category(data["Status Code"], data["Info (Klartext)"])
        
        # 2. Zähler aktualisieren
        if category == "Zugestellt": 
            count_delivered += 1
        elif category == "Unterwegs": 
            count_transit += 1
        else: 
            count_exception += 1
        
        # 3. Daten für die Tabelle aufbereiten
        row = {
            "Sendungsnummer": num,
            "Status": data["Status Code"],
            "Info": uebersetze_status(data["Status Code"]) if data["Status Code"] in STATUS_MAPPING else data["Info (Klartext)"],
            "Zeitpunkt": data["Zeitpunkt"],
            "Ort": data["Ort"],
            "Kategorie": category
        }
        results.append(row)
            
        # UI Updates
        with metrics_placeholder.container(): # Metriken in Platzhalter rendern
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Gesamt", len(nummern_liste))
            k2.metric("✅ Zugestellt", count_delivered)
            k3.metric("🚚 Unterwegs", count_transit)
            k4.metric("⚠️ Problem", count_exception)
                
        df = pd.DataFrame(results)
        table_placeholder.dataframe(
            df, 
            column_order=["Sendungsnummer", "Info", "Zeitpunkt", "Ort"],
            use_container_width=True, 
            hide_index=True
        )
            
        time.sleep(0.1)
        
    return df

# --- CALLBACK-FUNKTION FÜR DEN CACHE-RESET ---
def set_reset_flag():
    """Setzt ein Flag, das den Haupt-Code zwingt, den Cache zu löschen."""
    st.session_state.needs_reset = True

# =======================================================
# 3. UI LAYOUT & INTERAKTION
# =======================================================

st.set_page_config(page_title="DHL Dashboard", page_icon="📦", layout="centered") 

def set_filter(status):
    """Callback-Funktion, um den Filter-Status zu setzen."""
    st.session_state.filter_status = status

# --- HAUPTINHALT ---
with st.container():
    st.title("📦 DHL Tracking Dashboard")
    st.caption("Interaktives Tracking für nationale und internationale Sendungen")
    st.markdown("---")

    # --- 1. SCHNELL-CHECK ---
    st.subheader("⚡ Schnell-Check")
    col1, col2 = st.columns([4, 1])
    with col1:
        manual_input = st.text_input("Sendungsnummer eingeben", label_visibility="collapsed", placeholder="003404347800...")
    with col2:
        start_manual = st.button("🔍 Prüfen", use_container_width=True, type="primary")

    if start_manual and manual_input:
        st.markdown("### Ergebnis")
        man_table = st.empty()
        
        process_batch([manual_input], None, st.empty(), st.empty(), man_table) 

    st.markdown("---")

    # --- 2. MASSEN-VERARBEITUNG (PDF) ---
    st.subheader("📂 Massen-Verarbeitung (PDF)")
    
    # NEU: Callback auf den Upload-Button setzen, um den Reset vorzubereiten
    uploaded_file = st.file_uploader("PDF hier ablegen", type="pdf", on_change=set_reset_flag)

    # WICHTIG: Reset-Logik muss VOR dem Laden der Nummer kommen!
    if st.session_state.needs_reset:
        st.cache_data.clear() 
        st.session_state.data_frame = pd.DataFrame()
        st.session_state.filter_status = 'Gesamt'
        st.session_state.needs_reset = False
        st.rerun() 

    if uploaded_file:
        
        with st.spinner("Lese PDF..."):
            nummern = extrahiere_sendungsnummern(io.BytesIO(uploaded_file.read()), TRACKING_REGEX)
        
        if nummern:
            st.success(f"**{len(nummern)} Sendungsnummern** gefunden.")
            
            # --- AUTOMATISCHER START (Nur wenn Datenframe leer ist) ---
            if st.session_state.data_frame.empty:
                
                # UI-Platzhalter für die Live-Anzeige erstellen
                prog = st.empty()
                metrics = st.empty()
                table = st.empty()

                # Caching-Funktion aufrufen (Startet sofort)
                df_results = process_batch(
                    nummern, 
                    _time_stamp_identifier=time.time(), 
                    _progress_bar=prog, 
                    _metrics_placeholder=metrics, 
                    _table_placeholder=table
                )
                
                # NACH Abfrage: Daten speichern und Platzhalter leeren
                st.session_state.data_frame = df_results
                st.session_state.filter_status = 'Gesamt'
                prog.empty()
                metrics.empty() 
                table.empty() 
            
            # Ergebnisse nur anzeigen, wenn der Prozess einmal gelaufen ist
            if not st.session_state.data_frame.empty:
                df = st.session_state.data_frame

                # --- Metriken und Filter (ANZEIGEBLOCK) ---
                
                # 1. Metriken berechnen
                total = len(df)
                zugestellt = len(df[df['Kategorie'] == 'Zugestellt'])
                unterwegs = len(df[df['Kategorie'] == 'Unterwegs'])
                problem = len(df[df['Kategorie'] == 'Problem'])
                
                # 2. Metriken und Filter-Buttons rendern (über der Tabelle)
                st.markdown("### 📊 Gefilterter Status")
                
                # Filter-Metriken (Buttons)
                colA, colZ, colU, colP = st.columns(4)

                colA.button(f"**Gesamt**\n{total}", on_click=set_filter, args=('Gesamt',), key="btn_Gesamt", use_container_width=True)
                colZ.button(f"**✅ Zugestellt**\n{zugestellt}", on_click=set_filter, args=('Zugestellt',), key="btn_Zugestellt", use_container_width=True)
                colU.button(f"**🚚 Unterwegs**\n{unterwegs}", on_click=set_filter, args=('Unterwegs',), key="btn_Unterwegs", use_container_width=True)
                colP.button(f"**⚠️ Problem**\n{problem}", on_click=set_filter, args=('Problem',), key="btn_Problem", use_container_width=True)
                
                st.info(f"Aktuelle Anzeige: **{st.session_state.filter_status}**")

                # --- 3. Filterung anwenden und Tabelle rendern ---
                filter_status = st.session_state.filter_status
                
                if filter_status == 'Gesamt':
                    df_filtered = df
                else:
                    df_filtered = df[df['Kategorie'] == filter_status]
                
                # Tabelle anzeigen
                st.dataframe(df_filtered, use_container_width=True, hide_index=True)

                # CSV Download
                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button("📥 Als CSV herunterladen", data=csv, file_name='tracking_report.csv', mime='text/csv', use_container_width=True)
        else:
            st.warning("Keine Nummern gefunden.")