import streamlit as st
import requests
import json
import base64
import io
from datetime import datetime, timedelta, timezone 
from requests.auth import HTTPBasicAuth
from pypdf import PdfWriter, PdfReader

# --- KONFIGURATION ---
BILLBEE_API_KEY = "C0E4C2BB-8891-4964-8534-266EB7EA165D"
USERNAME = "info@rohmac.de"
PASSWORD = "Ex1t4Money#2025"
BASE_URL = "https://api.billbee.io/api/v1"

# 🎯 FILTER
SEARCH_TERM = "b762ad"

# ✅ KORRIGIERTE IDs AUS API-ANTWORT
SHIPPING_PRODUCT_ID = 100000000309374 
SHIPPING_PROVIDER_ID = 100000000024028 
DEFAULT_WEIGHT_GRAMS = 500 

# --- HELFER ---
def get_auth(): return HTTPBasicAuth(USERNAME, PASSWORD)
def get_headers(): return { "X-Billbee-Api-Key": BILLBEE_API_KEY, "Content-Type": "application/json" }


def get_label_pdf_from_billbee(shipping_id):
    """Führt einen sekundären GET-Request zum Abrufen des Label-PDFs aus."""
    url = f"{BASE_URL}/shipment/shippingdocuments/{shipping_id}"
    
    try:
        r = requests.get(url, headers=get_headers(), auth=get_auth())
        if r.status_code == 200 and r.headers.get('Content-Type') == 'application/pdf':
            return r.content, None
        else:
            return None, f"Sekundärer Download fehlgeschlagen. Status: {r.status_code}"
    except Exception as e:
        return None, f"Sekundärer Download gecrasht: {str(e)}"


def create_label_api(order_id, order_nr, address_data):
    """ 
    Erstellt das Label und versucht intelligent, an das PDF zu kommen 
    (direkt, per URL oder via sekundärem Download über ID).
    """
    url = f"{BASE_URL}/shipment/shipwithlabel"
    ship_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    payload = {
        "OrderId": int(order_id),
        "ProviderId": SHIPPING_PROVIDER_ID,
        "ProductId": SHIPPING_PRODUCT_ID,
        "ChangeStateToSend": True, 
        "WeightInGram": DEFAULT_WEIGHT_GRAMS,
        "ShipDate": ship_date, 
        "ClientReference": str(order_nr),
        
        "GetLabelData": True,
        
        "Dimension": { "length": 20, "width": 15, "height": 5 },
    }
    
    try:
        r = requests.post(url, json=payload, headers=get_headers(), auth=get_auth())
        
        try: resp = r.json()
        except: return None, f"Server-Crash bei Order {order_nr}: {r.text}", payload

        if r.status_code in [200, 201]:
            if resp.get('ErrorMessage'): return None, f"Billbee API Fehler: {resp.get('ErrorMessage')}", payload
            
            data_obj = resp.get('Data', {})
            shipping_id = data_obj.get('ShippingId')

            # 1. Versuch: Haben wir direkt die Daten (Base64)?
            pdf_base64_string = data_obj.get('LabelData') 
            if pdf_base64_string: 
                return base64.b64decode(pdf_base64_string), None, payload
            
            # 2. Versuch: Haben wir einen Download-Link?
            pdf_url = data_obj.get('LabelUrl')
            if pdf_url:
                try:
                    pdf_download = requests.get(pdf_url)
                    if pdf_download.status_code == 200:
                        return pdf_download.content, None, payload
                    else:
                        return None, f"Label erstellt, aber Download fehlgeschlagen (Link: {pdf_url})", payload
                except Exception as dl_err:
                     return None, f"Label erstellt, Link gefunden, aber Download-Fehler: {dl_err}", payload

            # 3. Fall: Label erstellt, aber keine Daten zurückgeliefert (jetzt sekundär herunterladen)
            if shipping_id:
                pdf_bytes_secondary, err_secondary = get_label_pdf_from_billbee(shipping_id)
                if pdf_bytes_secondary:
                    return pdf_bytes_secondary, None, payload
                else:
                    return None, f"Label erstellt (ID: {shipping_id}), aber Download misslungen: {err_secondary}", payload
            
            # 4. Fall: Komplett gescheitert
            return None, f"Kein LabelData/URL/ShippingID erhalten. Antwort: {json.dumps(data_obj)}", payload

        else:
            msg = resp.get('ErrorMessage') or resp.get('Message') or r.text
            return None, f"Fehler ({r.status_code}): {msg}", payload
    except Exception as e:
        return None, str(e), payload


# --- DATUMS HELFER ---
def extract_order_date(order):
    date_fields = ['CreatedAt', 'OrderDate', 'CreationDate', 'ShippedAt']
    order_date_raw = None

    for field in date_fields:
        date_candidate = order.get(field)
        if date_candidate:
            order_date_raw = date_candidate
            break
    
    if order_date_raw and len(order_date_raw) >= 10:
        try:
            return datetime.strptime(order_date_raw[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            return order_date_raw[:10]
    
    return "N/A" 


def fetch_and_group_data(days_back):
    date_str = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    url = f"{BASE_URL}/orders?minOrderDate={date_str}&page=1&pageSize=250&orderStateId=3"
    
    groups = { "TSM-M": [], "TSM-Rund": [], "TSM": [] }
    flat_rows = []

    try:
        r = requests.get(url, headers=get_headers(), auth=get_auth())
        if r.status_code != 200: return {}, []
        
        data_json = r.json()
        if 'Data' not in data_json: return {}, []

        for order in data_json['Data']:
            if order.get('ShippedAt'): continue
            if SEARCH_TERM.lower() not in json.dumps(order).lower(): continue
            dist_center = str(order.get('DistributionCenter', '')).lower()
            if "amazon" in dist_center or "fba" in dist_center: continue

            order_date = extract_order_date(order)
            
            customer = order.get('Customer', {}).get('Name', 'Unbekannt')
            order_nr = order.get('OrderNumber')
            oid = order.get('BillBeeOrderId')
            shipping_addr = order.get('ShippingAddress') or order.get('InvoiceAddress') or {}
            
            first = shipping_addr.get('FirstName', '')
            last = shipping_addr.get('LastName', '')
            comp = shipping_addr.get('Company', '')
            full_name = f"{first} {last}".strip() or comp or "Unbekannt"

            items_desc = []
            qty_sum = 0
            order_bucket = "TSM"
            
            for item in order.get('OrderItems', []):
                prod = item.get('Product', {})
                title = prod.get('Title') or item.get('Title') or "Unbekannt"
                sku = prod.get('SKU') or item.get('Sku') or ""
                qty = int(item.get('Quantity', 0))
                qty_sum += qty
                
                attrs = item.get('Attributes', [])
                if attrs: variant_str = " | ".join([str(a.get('Value', '')) for a in attrs])
                else: variant_str = ""
                
                items_desc.append({"sku": sku, "title": title, "variant": variant_str, "qty": qty})

                check_text = (title + " " + variant_str).lower()
                if "tsm-m" in check_text: order_bucket = "TSM-M"
                elif ("tsm-r" in check_text or "rundeecken" in check_text):
                    if order_bucket != "TSM-M": order_bucket = "TSM-Rund"

            row_data = {
                "id": oid, "nr": order_nr, "customer": full_name, "addr": shipping_addr, "items": items_desc, 
                "total_qty": qty_sum, "date": order_date
            }
            flat_rows.append(row_data)
            if order_bucket in groups: groups[order_bucket].append(row_data)
            else: groups["TSM"].append(row_data)
            
    except Exception as e: st.error(f"Fehler: {e}")
    return groups, flat_rows

# --- UI START ---
st.set_page_config(page_title="Pack & Ship Dashboard", layout="wide")
st.markdown("""<style>
.summary-card { padding: 15px; border-radius: 8px; text-align: center; color: white; font-weight: bold; margin-bottom: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
.card-value { font-size: 2.5em; line-height: 1.1; }
.qty-badge { background:#0055ff; color:white; padding:2px 8px; border-radius:4px; font-weight:bold; }
.var-badge { background:#e0e7ff; color:#3730a3; padding:2px 6px; border-radius:4px; font-size:0.9em; font-weight:600; margin-left:5px;}
</style>""", unsafe_allow_html=True)

c1, c2 = st.columns([6, 1])
c1.title("📦 Pack & Ship - Batch Mode")
if c2.button("🔄 Reload"): st.rerun()

with st.spinner("Lade Daten..."):
    grouped_data, flat_data = fetch_and_group_data(7)

if not flat_data:
    st.success("✅ Alles erledigt!")
else:
    
    st.markdown("---") 
    
    cnt_orders = {k: len(v) for k, v in grouped_data.items()}
    total_orders = sum(cnt_orders.values())
    
    cols = st.columns(4)
    with cols[0]: st.markdown(f'<div class="summary-card" style="background:#2563eb;">TSM-M<div class="card-value">{cnt_orders["TSM-M"]}</div></div>', unsafe_allow_html=True)
    with cols[1]: st.markdown(f'<div class="summary-card" style="background:#dc2626;">TSM-Rund<div class="card-value">{cnt_orders["TSM-Rund"]}</div></div>', unsafe_allow_html=True)
    with cols[2]: st.markdown(f'<div class="summary-card" style="background:#16a34a;">TSM<div class="card-value">{cnt_orders["TSM"]}</div></div>', unsafe_allow_html=True)
    with cols[3]: st.markdown(f'<div class="summary-card" style="background:#111827;">GESAMT<div class="card-value">{total_orders}</div></div>', unsafe_allow_html=True)
    st.markdown("---")

    for grp in ["TSM-M", "TSM-Rund", "TSM"]:
        orders = grouped_data[grp]
        if not orders: continue
        
        icon = {"TSM-M":"🔵", "TSM-Rund":"🔴", "TSM":"🟢"}[grp]
        
        with st.expander(f"{icon} {grp} ({len(orders)} Bestellungen)", expanded=True):
            
            # --- BATCH BUTTON AREA ---
            batch_key = f"batch_pdf_{grp}"
            
            if batch_key in st.session_state:
                st.success(f"✅ {len(orders)} Labels erfolgreich zusammengefügt!")
                st.download_button(
                    label=f"⬇️ Sammel-PDF herunterladen ({grp})",
                    data=st.session_state[batch_key],
                    file_name=f"Labels_{grp}_{datetime.now().strftime('%H%M')}.pdf",
                    mime="application/pdf",
                    key=f"dl_btn_{grp}"
                )
                if st.button("Zurücksetzen / Neu erstellen", key=f"reset_{grp}"):
                    del st.session_state[batch_key]
                    st.rerun()
            
            else:
                if st.button(f"🖨️ Alle {len(orders)} Labels für {grp} erstellen", key=f"gen_{grp}", type="primary"):
                    
                    merger = PdfWriter()
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    errors = []
                    
                    for idx, o in enumerate(orders):
                        status_text.text(f"Erstelle Label {idx+1} von {len(orders)}: {o['nr']}...")
                        
                        pdf_bytes, err_msg, debug_load = create_label_api(o['id'], o['nr'], o['addr'])
                        
                        if pdf_bytes:
                            reader = PdfReader(io.BytesIO(pdf_bytes))
                            for page in reader.pages:
                                merger.add_page(page)
                        else:
                            errors.append(f"Order #{o['nr']}: {err_msg}")
                        
                        progress_bar.progress((idx + 1) / len(orders))
                    
                    output_stream = io.BytesIO()
                    merger.write(output_stream)
                    final_pdf = output_stream.getvalue()
                    
                    st.session_state[batch_key] = final_pdf
                    
                    if errors:
                        st.error(f"Es gab Fehler bei {len(errors)} Aufträgen:")
                        st.json(errors) 
                    
                    st.rerun()

            # --- TABELLE (mit Bestellnummer und Bestelldatum) ---
            st.divider()
            
            # 🔥 ANGEPASSTE SPALTEN: [0.8, 3.5, 2.5, 0.8, 0.8]
            c1, c2, c3, c4, c5 = st.columns([0.8, 3.5, 2.5, 0.8, 0.8]) 
            c1.markdown("**Anz.**"); c2.markdown("**Artikel**"); c3.markdown("**Kunde & Adresse**"); c4.markdown("**Datum**"); c5.markdown("**Bestell-Nr.**")
            
            for o in orders:
                addr = o['addr']
                street = addr.get('Street', '')
                house_number = addr.get('HouseNumber', '')
                zip_code = addr.get('Zip', '')
                city = addr.get('City', '')
                country = addr.get('CountryCode', '')
                
                # ADRESS-FORMATIERUNG (HTML für Zeilenumbrüche)
                address_html = f"<small style='line-height:1.2; opacity:0.8;'>{street} {house_number}<br>{zip_code} {city}"
                if country and country.upper() != "DE":
                    address_html += f"<br>**{country}**"
                address_html += "</small>"

                # ANGEPASSTE SPALTEN: Werte
                c1, c2, c3, c4, c5 = st.columns([0.8, 3.5, 2.5, 0.8, 0.8])
                with c1: st.markdown(f"**{o['total_qty']}x**")
                with c2:
                    for i in o['items']:
                        v_html = f'<span class="var-badge">{i["variant"]}</span>' if i["variant"] else ""
                        st.markdown(f"{i['qty']}x **{i['sku']}** | {i['title']} {v_html}", unsafe_allow_html=True)
                with c3: 
                    st.markdown(f"👤 **{o['customer']}**<br>{address_html}", unsafe_allow_html=True) 
                with c4: st.markdown(f"📅 **{o['date']}**") 
                with c5: st.markdown(f"**#{o['nr']}**") 
                st.markdown("<hr style='margin:2px 0; opacity:0.3'>", unsafe_allow_html=True)