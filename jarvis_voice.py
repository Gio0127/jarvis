"""Jarvis voce: "Hey Jarvis" -> trascrizione -> risposta a voce, con HUD olografico a schermo intero."""
import ctypes
import json
import math
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QLockFile, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QGuiApplication, QIcon, QLinearGradient, QPainter, QPen,
                           QPixmap, QRadialGradient)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

SOGLIA = 0.5       # sensibilita': abbassa se non ti sente, alza se si sveglia da solo
CAMPIONI = 1280    # 80 ms a 16 kHz: il blocco che openWakeWord si aspetta
SILENZIO = 50      # volume sotto cui e' silenzio (tu parli a ~150, il fondo e' ~15)
PAUSA_FINE = 10    # 10 blocchi x 80 ms = 0,8 s di silenzio chiudono la frase
MAX_BLOCCHI = 375  # registrazione massima: 30 s (la frase si chiude comunque alla prima pausa)
MIN_VOCE = 5       # servono almeno 5 blocchi (0,4 s) di voce: la coda di "Jarvis" non basta
ATTESA_SEGUITO = 75  # dopo una risposta resta in ascolto 75 x 80 ms = 6 s senza bisogno di "Hey Jarvis"
MAX_STORIA = 8       # messaggi della conversazione in corso inviati a Jarvis (4 domande + 4 risposte)

OLLAMA = "http://127.0.0.1:11434/api/chat"  # Ollama in locale: 'jarvis serve' non serve per la voce
CONTESTO_LLM = 8192  # token di contesto: prompt + dati in tempo reale + conversazione

# Dati personali: stanno in impostazioni_personali.json, escluso da git (modello: impostazioni_personali.esempio.json)
CARTELLA = os.path.dirname(os.path.abspath(__file__))
IMPOSTAZIONI_FILE = os.path.join(CARTELLA, "impostazioni_personali.json")
if not os.path.exists(IMPOSTAZIONI_FILE):
    sys.exit("Manca impostazioni_personali.json: copialo da impostazioni_personali.esempio.json e compilalo.")
with open(IMPOSTAZIONI_FILE, encoding="utf-8") as f:
    IMP = json.load(f)

NOME = IMP["nome"]
MODELLO_LLM = IMP["modello_llm"]
VOCE = IMP["voce"]
VELOCITA = IMP["velocita"]
CITTA, LAT, LON = IMP["citta"], IMP["lat"], IMP["lon"]  # per il meteo
SPOTIFY_CLIENT_ID = IMP["spotify_client_id"]  # Client ID della tua app Spotify (vuoto per non usarla)

PROMPT_JARVIS = f"""Sei Jarvis, l'assistente AI personale di {NOME}. Giri in locale sul suo PC: non sei un servizio cloud e non sei Claude, ChatGPT, Gemini o altri assistenti. Se ti chiedono chi sei, rispondi che sei Jarvis.

PERSONALITÀ
- Elegante, calmo e con un'ironia sottile e garbata, come un maggiordomo britannico impeccabile.
- Rivolgiti sempre direttamente a {NOME} dandogli del Lei e chiamandolo signore; non parlare mai di lui in terza persona. Presentati solo se ti chiede chi sei: altrimenti rispondi subito alla domanda, senza formule di apertura.
- L'ironia non deve mai rallentare l'aiuto: prima la sostanza, poi, se ci sta, una battuta breve.

AZIONI SUL PC
- Hai degli strumenti per aprire programmi, cartelle e siti (YouTube, Prime Video, Netflix, DAZN, NOW), riprodurre musica su Spotify, controllare la musica, regolare il volume e bloccare lo schermo: usali quando {NOME} te lo chiede, senza chiedere conferma.
- Puoi anche mandare messaggi WhatsApp e fare chiamate ai contatti della rubrica: usa gli strumenti, la conferma a {NOME} la chiede il sistema. Scrivi il messaggio in prima persona come se lo scrivesse {NOME}, fedele a quello che ha detto, senza firma. Per le chiamate usa il modo che indica lui (telefono o WhatsApp); se non lo dice, usa telefono.
- Se chiede un programma o una cartella che non è tra quelli disponibili, diglielo.

MODALITÀ VOCALE
- Le tue risposte vengono lette ad alta voce: rispondi in modo breve, di solito 1-3 frasi, a meno che {NOME} chieda dettagli.
- Usa frasi naturali da dire a voce: niente elenchi, tabelle, simboli, emoji o blocchi di codice.

STILE
- Rispondi sempre in italiano, in modo diretto e pratico.
- Se una richiesta è ambigua, fai una sola domanda di chiarimento mirata.
- Se non sai qualcosa o non sei sicuro, dillo chiaramente invece di inventare.

CHI È {NOME.upper()}
""" + "\n".join(f"- {riga}" for riga in IMP["profilo"]) + f"""

PERSONE IMPORTANTI PER {NOME.upper()}
""" + "\n".join(f"- {riga}" for riga in IMP["persone"]) + f"""
- Quando {NOME} dice "mia madre", "la mia fidanzata", "mio cognato" e simili sai di chi parla: usa i nomi in modo naturale, senza elencare queste informazioni se non servono.

AMBITO CAF E PATRONATO
- Su temi fiscali e previdenziali italiani (730, ISEE, IMU, pensioni, NASpI, bonus) dai indicazioni pratiche, ma ricorda sempre che norme, scadenze e importi cambiano spesso e vanno verificati sulle fonti ufficiali (Agenzia delle Entrate, INPS). Non inventare mai riferimenti normativi, aliquote o scadenze."""

SPOTIFY_REDIRECT = "http://127.0.0.1:8888/callback"

SOGLIA_TEMP_GPU = 83    # gradi: la 4070 rallenta intorno ai 90
SOGLIA_CPU = 95         # % per almeno 60 s
SOGLIA_RAM = 92         # % per almeno 30 s
SOGLIA_DISCO = 10       # % di spazio libero minimo su C:

# Stato condiviso tra i thread e l'HUD
stato = "avvio"      # avvio | riposo | ascolto | pensa | parla
livello_mic = 0.0    # volume del microfono, 0..1
voce_env = None      # inviluppo della frase che Jarvis sta dicendo (30 valori al secondo)
voce_t0 = 0.0        # istante di inizio della frase
passi_avvio = []     # righe della sequenza di avvio, aggiunte man mano che i modelli si caricano
N_PASSI = 5 if SPOTIFY_CLIENT_ID else 4
spotify = None       # client Spotify, pronto dopo l'accesso
dati = {"cpu": 0.0, "gpu": None, "vram": None, "temp": None, "ram": 0.0, "disco": 100.0,
        "meteo": "--", "previsioni": []}
avvisi = []          # avvisi sul PC da dire a voce appena Jarvis e' libero
nodi_attivi = {}     # nodo della rete -> istante in cui si e' acceso (capacita' usata o tema della domanda)
sottotitolo = None   # (parole, istanti di inizio, t0) della frase che Jarvis sta dicendo


# ---------------------------------------------------------------- voce ----

def registra_frase(microfono, attesa=None):
    """Registra finche' non fai una pausa dopo aver parlato. None se non hai parlato.
    attesa: se dato, blocchi entro cui devi iniziare a parlare (finestra di continuazione)."""
    global livello_mic
    blocchi, silenzi, voce = [], 0, 0
    for n in range(MAX_BLOCCHI + (attesa or 0)):
        if attesa is not None and voce == 0 and n >= attesa:
            return None
        blocco, _ = microfono.read(CAMPIONI)
        blocchi.append(blocco[:, 0])
        volume = np.abs(blocco).mean()
        livello_mic = min(1.0, volume / 300)
        if volume > SILENZIO:
            voce, silenzi = voce + 1, 0
        else:
            silenzi += 1
        if voce >= MIN_VOCE and silenzi >= PAUSA_FINE:
            break
    if voce < MIN_VOCE:
        return None
    return np.concatenate(blocchi).astype(np.float32) / 32768.0


def contesto():
    """Informazioni in tempo reale da passare a Jarvis insieme alla domanda."""
    ora = datetime.now()
    righe = [f"Data e ora: {GIORNI[ora.weekday()]} {ora.day} {MESI[ora.month - 1]} {ora.year}, ore {ora:%H:%M}",
             f"Citta' di {NOME}: {CITTA}",
             f"Meteo attuale a {CITTA}: {dati['meteo']}"]
    righe += [f"Previsioni {p}" for p in dati["previsioni"]]
    righe.append(f"PC di {NOME}: CPU {dati['cpu']:.0f}%, RAM {dati['ram']:.0f}%, spazio libero su C: {dati['disco']:.0f}%")
    if dati["gpu"] is not None:
        usata, totale = dati["vram"]
        righe[-1] += f", GPU {dati['gpu']}%, VRAM {usata:.1f}/{totale:.0f} GB, temperatura GPU {dati['temp']} gradi"
    return "\n".join(righe)


# ------------------------------------------------------------- azioni ----

# Programmi che Jarvis puo' aprire: nome detto -> cosa avviare (aggiungine pure altri)
APP = {"chrome": "chrome.exe", "edge": "msedge.exe", "blocco note": "notepad.exe", "calcolatrice": "calc.exe",
       "esplora risorse": "explorer.exe", "impostazioni": "ms-settings:", "gestione attivita": "taskmgr.exe",
       "spotify": "spotify:", "discord": "discord:", "steam": "steam:",
       "word": "winword.exe", "excel": "excel.exe", "outlook": "outlook.exe"}
# Cartelle che Jarvis puo' aprire (i percorsi shell: funzionano anche con OneDrive)
CARTELLE = {"documenti": "shell:Personal", "download": "shell:Downloads", "desktop": "shell:Desktop",
            "immagini": "shell:My Pictures", "musica": "shell:My Music", "video": "shell:My Video",
            "documenti di jarvis": r"C:\JarvisDocs"}
# Rubrica: file rubrica.json accanto allo script, nel formato {"Nome": "+39 333 1234567", ...}
RUBRICA_FILE = os.path.join(CARTELLA, "rubrica.json")
ATTESA_WHATSAPP = 10  # secondi massimi per far comparire la chat di WhatsApp prima di premere Invio


def carica_rubrica():
    """Legge la rubrica; la prima volta crea il file con un contatto di esempio da compilare."""
    if not os.path.exists(RUBRICA_FILE):
        with open(RUBRICA_FILE, "w", encoding="utf-8") as f:
            json.dump({"Nome": ""}, f, indent=2, ensure_ascii=False)
    with open(RUBRICA_FILE, encoding="utf-8") as f:
        return json.load(f)


RUBRICA = carica_rubrica()

# Siti che Jarvis apre in Chrome
SITI = {"youtube": "https://www.youtube.com", "prime video": "https://www.primevideo.com",
        "netflix": "https://www.netflix.com", "dazn": "https://www.dazn.com", "now tv": "https://www.nowtv.it"}

STRUMENTI = [
    {"type": "function", "function": {
        "name": "apri_app", "description": f"Apre un programma sul PC di {NOME}",
        "parameters": {"type": "object", "required": ["nome"],
                       "properties": {"nome": {"type": "string", "enum": list(APP)}}}}},
    {"type": "function", "function": {
        "name": "apri_cartella", "description": "Apre una cartella in Esplora risorse",
        "parameters": {"type": "object", "required": ["nome"],
                       "properties": {"nome": {"type": "string", "enum": list(CARTELLE)}}}}},
    {"type": "function", "function": {
        "name": "apri_sito", "description": "Apre un sito di video o streaming in Google Chrome",
        "parameters": {"type": "object", "required": ["sito"],
                       "properties": {"sito": {"type": "string", "enum": list(SITI)},
                                      "ricerca": {"type": "string", "description": "cosa cercare, solo per YouTube"}}}}},
    {"type": "function", "function": {
        "name": "spotify", "description": "Riproduce su Spotify un brano, un artista, un album o una playlist",
        "parameters": {"type": "object", "required": ["cosa"],
                       "properties": {"cosa": {"type": "string", "description": "cosa riprodurre, es. titolo e artista"},
                                      "tipo": {"type": "string", "enum": ["brano", "artista", "album", "playlist"]}}}}},
    {"type": "function", "function": {
        "name": "controllo_musica", "description": "Controlla la musica o il video in riproduzione",
        "parameters": {"type": "object", "required": ["azione"],
                       "properties": {"azione": {"type": "string",
                                                 "enum": ["pausa o riprendi", "successiva", "precedente"]}}}}},
    {"type": "function", "function": {
        "name": "volume", "description": "Regola il volume del PC",
        "parameters": {"type": "object", "required": ["azione"],
                       "properties": {"azione": {"type": "string", "enum": ["alza", "abbassa", "muto", "imposta"]},
                                      "livello": {"type": "integer", "description": "da 0 a 100, solo per 'imposta'"}}}}},
    {"type": "function", "function": {
        "name": "invia_whatsapp", "description": "Manda un messaggio WhatsApp a un contatto della rubrica",
        "parameters": {"type": "object", "required": ["contatto", "messaggio"],
                       "properties": {"contatto": {"type": "string", "enum": list(RUBRICA)},
                                      "messaggio": {"type": "string", "description": f"testo in prima persona, come lo scriverebbe {NOME}"}}}}},
    {"type": "function", "function": {
        "name": "chiama", "description": "Chiama un contatto della rubrica, al telefono o su WhatsApp",
        "parameters": {"type": "object", "required": ["contatto"],
                       "properties": {"contatto": {"type": "string", "enum": list(RUBRICA)},
                                      "modo": {"type": "string", "enum": ["telefono", "whatsapp"]}}}}},
    {"type": "function", "function": {
        "name": "blocca_schermo", "description": "Blocca il PC mostrando la schermata di accesso",
        "parameters": {"type": "object", "properties": {}}}},
]


def tasto(codice, volte=1):
    """Simula la pressione di un tasto multimediale di Windows."""
    for _ in range(volte):
        ctypes.windll.user32.keybd_event(codice, 0, 0, 0)
        ctypes.windll.user32.keybd_event(codice, 0, 2, 0)


VOL_SU, VOL_GIU, VOL_MUTO = 0xAF, 0xAE, 0xAD  # ogni pressione cambia il volume del 2%
MEDIA = {"pausa o riprendi": 0xB3, "successiva": 0xB0, "precedente": 0xB1}  # tasti multimediali


TIPI_SPOTIFY = {"brano": "track", "artista": "artist", "album": "album", "playlist": "playlist"}


def collega_spotify():
    """Accesso a Spotify (la prima volta apre il browser per autorizzare Jarvis)."""
    global spotify
    import spotipy
    from spotipy.oauth2 import SpotifyPKCE
    auth = SpotifyPKCE(client_id=SPOTIFY_CLIENT_ID, redirect_uri=SPOTIFY_REDIRECT,
                       scope="user-read-playback-state user-modify-playback-state",
                       cache_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".spotify_token"))
    client = spotipy.Spotify(auth_manager=auth)
    client.current_user()  # verifica l'accesso
    spotify = client


def dispositivo_spotify():
    """ID di Spotify su questo PC; se Spotify e' chiuso lo apre e aspetta che sia pronto."""
    for tentativo in range(8):
        dispositivi = spotify.devices()["devices"]
        pc = [d for d in dispositivi if d["type"] == "Computer"] or dispositivi
        if pc:
            return pc[0]["id"]
        if tentativo == 0:
            os.startfile("spotify:")
        time.sleep(1.5)
    return None


def riproduci_spotify(cosa, tipo):
    """Cerca su Spotify e fa partire subito il primo risultato."""
    tipo_api = TIPI_SPOTIFY.get(tipo, "track")
    trovati = [x for x in spotify.search(q=cosa, type=tipo_api, limit=3)[tipo_api + "s"]["items"] if x]
    if not trovati:
        return f"Non ho trovato {cosa} su Spotify, signore."
    elemento = trovati[0]
    dispositivo = dispositivo_spotify()
    if dispositivo is None:
        return "Non trovo Spotify attivo su questo PC, signore. Lo apra e riprovi."
    if tipo_api == "track":
        spotify.start_playback(device_id=dispositivo, uris=[elemento["uri"]])
    else:
        spotify.start_playback(device_id=dispositivo, context_uri=elemento["uri"])
    autore = f" di {elemento['artists'][0]['name']}" if tipo_api in ("track", "album") else ""
    return f"Riproduco {elemento['name']}{autore}, signore."


azione_in_attesa = None  # messaggio o chiamata in attesa della conferma dell'utente: (nome, argomenti)
chiudi_conversazione = False  # dopo una chiamata Jarvis smette di ascoltare, per non intromettersi
SI = {"sì", "si", "invia", "invialo", "mandalo", "manda", "vai", "ok", "okay", "conferma", "confermo",
      "certo", "esatto", "procedi", "chiama", "chiamala", "chiamalo"}
NO = {"no", "non", "annulla", "lascia", "stop", "fermo", "niente"}


def numero(contatto):
    """Numero in sole cifre con prefisso internazionale (aggiunge 39 ai cellulari italiani)."""
    cifre = re.sub(r"\D", "", RUBRICA.get(contatto, ""))
    if len(cifre) == 10 and cifre.startswith("3"):
        cifre = "39" + cifre
    return cifre


def domanda_conferma(nome, arg):
    if nome == "invia_whatsapp":
        return f"Ho capito: scrivo a {arg['contatto']}: {arg['messaggio']}. Lo invio?"
    modo = "su WhatsApp" if arg.get("modo") == "whatsapp" else "al telefono"
    return f"Chiamo {arg['contatto']} {modo}. Confermi?"


def finestra_attiva():
    """Titolo della finestra in primo piano."""
    testo = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetWindowTextW(ctypes.windll.user32.GetForegroundWindow(), testo, 256)
    return testo.value


def esegui_confermata(nome, arg):
    """Esegue il messaggio o la chiamata dopo il si' dell'utente."""
    global chiudi_conversazione
    contatto, cifre = arg["contatto"], numero(arg["contatto"])
    if nome == "invia_whatsapp":
        os.startfile(f"whatsapp://send?phone={cifre}&text={urllib.parse.quote(arg['messaggio'])}")
        for _ in range(ATTESA_WHATSAPP * 2):  # aspetta che la chat di WhatsApp sia in primo piano
            if "whatsapp" in finestra_attiva().lower():
                time.sleep(1.0)  # tempo per caricare il testo nella casella
                tasto(0x0D)      # Invio
                return f"Messaggio inviato a {contatto}, signore."
            time.sleep(0.5)
        return f"Ho preparato il messaggio per {contatto}, ma WhatsApp non è in primo piano: prema lei Invio, signore."
    chiudi_conversazione = True  # da qui in poi si tratta di una chiamata
    if arg.get("modo") == "whatsapp":
        os.startfile(f"whatsapp://send?phone={cifre}")
        return f"Ho aperto la chat con {contatto}: prema il pulsante di chiamata, signore."
    try:
        os.startfile(f"tel:+{cifre}")
    except OSError:
        return "Non riesco ad avviare la chiamata, signore: controlli Collegamento al telefono."
    return f"Chiamo {contatto}, signore."


def gestisci_conferma(testo):
    """Interpreta la risposta dell'utente alla conferma: si', no, oppure 'cambia in ...'."""
    global azione_in_attesa
    nome, arg = azione_in_attesa
    cambio = re.match(r"\s*(?:cambia|correggi|modifica|scrivi invece)\w*[\s,:]+(?:in\s+|con\s+)?(.+)", testo, re.I)
    if nome == "invia_whatsapp" and cambio:
        nuovo = cambio.group(1).strip()
        arg["messaggio"] = nuovo[:1].upper() + nuovo[1:]
        return domanda_conferma(nome, arg)
    parole = set(re.sub(r"[^\w\s]", " ", testo.lower()).split())
    if parole & NO:  # il no ha sempre la precedenza: nel dubbio non si invia
        azione_in_attesa = None
        return "Annullato, signore."
    if parole & SI:
        azione_in_attesa = None
        return esegui_confermata(nome, arg)
    return "Non ho capito, signore. Confermo? Risponda sì o no."


# Quale nodo della rete si accende per ogni azione e per ogni tema della domanda
NODO_AZIONE = {"apri_app": "APP", "apri_cartella": "APP", "apri_sito": "WEB", "spotify": "MUSICA",
               "controllo_musica": "MUSICA", "volume": "SISTEMA", "blocca_schermo": "SISTEMA",
               "invia_whatsapp": "WHATSAPP", "chiama": "CHIAMATE"}
NODO_PAROLE = {"METEO": ["meteo", "tempo fa", "piov", "pioggia", "temperatura", "previsioni"],
               "SISTEMA": ["pc", "computer", "cpu", "gpu", "ram", "disco"],
               **{nodo: c["parole"] for nodo, c in IMP["conoscenze"].items()},  # temi personali
               "MEMORIA": ["ricord", "documenti", "memoria"]}


def accendi(nodo):
    if nodo:
        nodi_attivi[nodo] = time.time()


def esegui(nome, argomenti):
    """Esegue un'azione scelta dal modello e restituisce la frase di conferma da dire a voce."""
    print(f"[azione] {nome} {argomenti}")
    accendi(NODO_AZIONE.get(nome))
    if nome == "apri_app":
        app = argomenti.get("nome", "")
        if app not in APP:
            return f"Non ho {app} tra i programmi che posso aprire, signore."
        try:
            os.startfile(APP[app])
        except OSError:
            return f"Non riesco ad aprire {app}, signore: forse non è installato."
        return f"Apro {app}, signore."
    if nome == "apri_cartella":
        cartella = argomenti.get("nome", "")
        if cartella not in CARTELLE:
            return f"Non conosco la cartella {cartella}, signore."
        subprocess.Popen(["explorer", CARTELLE[cartella]])
        return f"Apro la cartella {cartella}, signore."
    if nome == "apri_sito":
        sito, ricerca = argomenti.get("sito", ""), argomenti.get("ricerca")
        if sito not in SITI:
            return f"Non ho {sito} tra i siti che posso aprire, signore."
        url = SITI[sito]
        if sito == "youtube" and ricerca:
            url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(ricerca)
        try:
            os.startfile("chrome.exe", arguments=url)
        except OSError:
            os.startfile(url)  # senza Chrome usa il browser predefinito
        return f"Cerco {ricerca} su YouTube, signore." if sito == "youtube" and ricerca else f"Apro {sito}, signore."
    if nome == "spotify":
        cosa = argomenti.get("cosa", "")
        if spotify is not None:
            try:
                return riproduci_spotify(cosa, argomenti.get("tipo", "brano"))
            except Exception as e:  # es. Spotify non raggiungibile: lo dice invece di bloccarsi
                print("Errore Spotify:", e)
                return "Spotify ha dato un errore, signore. Provo ad aprire la ricerca."
        try:
            os.startfile("spotify:search:" + urllib.parse.quote(cosa))
        except OSError:
            return "Non riesco ad aprire Spotify, signore: forse non è installato."
        return f"Ho cercato {cosa} su Spotify, signore: scelga pure cosa riprodurre."
    if nome == "controllo_musica":
        azione = argomenti.get("azione")
        if azione not in MEDIA:
            return "Non so eseguire questo comando sulla musica, signore."
        tasto(MEDIA[azione])
        return {"pausa o riprendi": "Fatto, signore.", "successiva": "Brano successivo, signore.",
                "precedente": "Brano precedente, signore."}[azione]
    if nome == "volume":
        azione = argomenti.get("azione")
        if azione == "alza":
            tasto(VOL_SU, 5)
            return "Volume alzato, signore."
        if azione == "abbassa":
            tasto(VOL_GIU, 5)
            return "Volume abbassato, signore."
        if azione == "muto":
            tasto(VOL_MUTO)
            return "Audio disattivato, signore. Lo ripeta per riattivarlo."
        if azione == "imposta":
            livello = max(0, min(100, int(argomenti.get("livello", 50))))
            tasto(VOL_GIU, 50)              # prima a zero...
            tasto(VOL_SU, round(livello / 2))  # ...poi al livello richiesto
            return f"Volume al {livello} per cento, signore."
    if nome in ("invia_whatsapp", "chiama"):
        contatto = argomenti.get("contatto", "")
        if contatto not in RUBRICA:
            return f"{contatto} non è nella sua rubrica, signore."
        if not numero(contatto):
            return f"Nella rubrica manca il numero di {contatto}, signore."
        global azione_in_attesa
        azione_in_attesa = (nome, dict(argomenti))
        return domanda_conferma(nome, argomenti)
    if nome == "blocca_schermo":
        ctypes.windll.user32.LockWorkStation()
        return "Blocco il computer, signore."
    return "Non so eseguire questa azione, signore."


def chiedi_a_jarvis(testo, storia):
    """Manda la domanda (con contesto in tempo reale e conversazione in corso) a Ollama e restituisce la risposta."""
    for nodo, parole in NODO_PAROLE.items():  # accende i nodi dei temi citati
        if any(re.search(r"\b" + re.escape(x), testo.lower()) for x in parole):
            accendi(nodo)
    domanda = (f"Informazioni in tempo reale (usale solo se servono a rispondere):\n{contesto()}\n\n"
               f"Domanda di {NOME}: {testo}")
    dati_json = json.dumps({
        "model": MODELLO_LLM,
        "messages": [{"role": "system", "content": PROMPT_JARVIS}] + storia + [{"role": "user", "content": domanda}],
        "tools": STRUMENTI,  # azioni sul PC che il modello puo' scegliere
        "think": False,      # niente ragionamento lungo: risposte rapide per la voce
        "stream": False,
        "options": {"num_ctx": CONTESTO_LLM},
    }).encode()
    richiesta = urllib.request.Request(OLLAMA, data=dati_json, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(richiesta, timeout=120) as r:
        messaggio = json.load(r)["message"]
    if messaggio.get("tool_calls"):  # il modello ha scelto una o piu' azioni sul PC
        return " ".join(esegui(c["function"]["name"], c["function"].get("arguments") or {})
                        for c in messaggio["tool_calls"])
    return re.sub(r"<think>.*?</think>", "", messaggio["content"], flags=re.S).strip()  # per sicurezza


def parla(voce_kokoro, testo, modo="parla"):
    """Legge il testo con Kokoro e pubblica l'inviluppo per l'animazione (modo: parla | avviso)."""
    global stato, voce_env, voce_t0, sottotitolo
    stato = modo
    testo = re.sub(r"[*#`_>]", "", testo)  # i simboli markdown non vanno letti
    inizio, prima = time.time(), True
    for frase, _, audio in voce_kokoro(testo, voice=VOCE, speed=VELOCITA, split_pattern=r"(?<=[.!?])\s+"):
        if prima:
            print(f"[tempi] prima frase vocale pronta: {time.time() - inizio:.1f} s")
            prima = False
        a = np.asarray(audio, dtype=np.float32)
        passo = 24000 // 30
        env = np.array([np.sqrt(np.mean(a[i:i + passo] ** 2)) for i in range(0, len(a), passo)])
        parole = frase.split()  # tempi stimati: ogni parola dura in proporzione alla sua lunghezza
        cumulata = np.cumsum([0] + [len(w) + 1 for w in parole])
        voce_env, voce_t0 = env / max(env.max(), 1e-3), time.time()
        sottotitolo = (parole, list(cumulata[:-1] / cumulata[-1] * len(a) / 24000), voce_t0)
        sd.play(a, 24000)
        sd.wait()
    voce_env = sottotitolo = None


def ciclo_voce():
    """Carica i modelli (sequenza di avvio), poi ascolta 'Hey Jarvis' e conversa."""
    global stato, livello_mic, azione_in_attesa, chiudi_conversazione
    passi_avvio.append("AVVIO SISTEMI PRINCIPALI")
    import openwakeword.utils
    from faster_whisper import WhisperModel
    from kokoro import KPipeline
    from openwakeword.model import Model

    openwakeword.utils.download_models(["hey_jarvis"])
    modello = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    passi_avvio.append("PAROLA DI ATTIVAZIONE ........ ONLINE")
    whisper = WhisperModel("small", device="cpu", compute_type="int8")
    passi_avvio.append("RICONOSCIMENTO VOCALE ........ ONLINE")
    voce_kokoro = KPipeline(lang_code="i", repo_id="hexgrad/Kokoro-82M")
    for _ in voce_kokoro("Pronto.", voice=VOCE):  # riscaldamento: carica voce e pronuncia subito
        pass
    passi_avvio.append("SINTESI VOCALE ............... ONLINE")
    if SPOTIFY_CLIENT_ID:
        try:
            collega_spotify()
            passi_avvio.append("SPOTIFY ...................... ONLINE")
        except Exception as e:  # senza Spotify Jarvis funziona comunque
            print("Spotify non collegato:", e)
            passi_avvio.append("SPOTIFY ...................... NON COLLEGATO")
    ora = datetime.now().hour
    saluto = "Buongiorno" if ora < 13 else "Buon pomeriggio" if ora < 18 else "Buonasera"
    parla(voce_kokoro, f"{saluto}, signore. Sistemi online.")
    stato = "riposo"

    print("In ascolto... di' 'Hey Jarvis' (Ctrl+C qui per uscire)")
    with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=CAMPIONI) as microfono:
        while True:
            if avvisi:  # anomalia sul PC: Jarvis avvisa appena e' libero
                messaggio = avvisi.pop(0)
                print("AVVISO:", messaggio)
                parla(voce_kokoro, messaggio, "avviso")
                stato = "riposo"
                microfono.read(microfono.read_available)
                modello.reset()
            blocco, _ = microfono.read(CAMPIONI)
            livello_mic = min(1.0, np.abs(blocco).mean() / 300)
            punteggio = max(modello.predict(blocco[:, 0]).values())
            if punteggio > SOGLIA:
                print(f"Sveglio! (punteggio {punteggio:.2f}) Ti ascolto...")
                storia, attesa = [], None  # nuova conversazione
                while True:  # dopo ogni risposta resta in ascolto, senza bisogno di "Hey Jarvis"
                    stato = "ascolto"
                    audio = registra_frase(microfono, attesa)
                    if audio is None:
                        print("Non ho sentito niente." if attesa is None else "Fine conversazione.")
                        break
                    stato = "pensa"
                    t1 = time.time()
                    segmenti, _ = whisper.transcribe(audio, language="it", vad_filter=True)
                    testo = " ".join(s.text.strip() for s in segmenti if s.no_speech_prob < 0.6)
                    print(f"[tempi] trascrizione: {time.time() - t1:.1f} s")
                    if "sottotitoli" in testo.lower():  # allucinazione tipica di Whisper sul silenzio
                        testo = ""
                    print("Hai detto:", testo if testo else "(non ho capito)")
                    if not testo:
                        break
                    try:
                        t2 = time.time()
                        risposta = gestisci_conferma(testo) if azione_in_attesa else chiedi_a_jarvis(testo, storia)
                        print(f"[tempi] risposta del modello: {time.time() - t2:.1f} s")
                        print("Jarvis:", risposta)
                        parla(voce_kokoro, risposta)
                    except urllib.error.URLError as e:
                        print(f"Ollama non raggiungibile ({e}): controlla che sia avviato (icona vicino all'orologio).")
                        break
                    if chiudi_conversazione:  # chiamata avviata: Jarvis non ascolta la telefonata
                        chiudi_conversazione = False
                        print("Chiamata avviata: conversazione con Jarvis chiusa.")
                        break
                    storia = (storia + [{"role": "user", "content": testo},
                                        {"role": "assistant", "content": risposta}])[-MAX_STORIA:]
                    microfono.read(microfono.read_available)  # scarta l'audio accumulato mentre parlava
                    attesa = ATTESA_SEGUITO
                    print(f"(puoi continuare: ti ascolto per {ATTESA_SEGUITO * CAMPIONI // 16000} secondi)")
                if azione_in_attesa:  # nessuna conferma: per sicurezza non si invia nulla
                    print("Azione annullata: nessuna conferma.")
                    azione_in_attesa = None
                stato = "riposo"
                microfono.read(microfono.read_available)  # scarta l'audio accumulato nel frattempo
                modello.reset()  # evita attivazioni ripetute sulla stessa frase


# ---------------------------------------------------------------- dati ----

METEO = {0: "Sereno", 1: "Poco nuvoloso", 2: "Parz. nuvoloso", 3: "Coperto", 45: "Nebbia", 48: "Nebbia",
         51: "Pioviggine", 53: "Pioviggine", 55: "Pioviggine", 61: "Pioggia", 63: "Pioggia", 65: "Pioggia forte",
         80: "Rovesci", 81: "Rovesci", 82: "Rovesci forti", 95: "Temporale", 96: "Temporale", 99: "Temporale"}


durata, segnalati = {}, set()


def controlla(chiave, anomalia, secondi, messaggio):
    """Accoda un avviso se l'anomalia dura da 'secondi' secondi. Si riarma solo quando il valore rientra."""
    if not anomalia:
        durata[chiave] = 0
        segnalati.discard(chiave)
        return
    durata[chiave] = durata.get(chiave, 0) + 1
    if durata[chiave] >= secondi and chiave not in segnalati:
        segnalati.add(chiave)
        avvisi.append(messaggio)


def ciclo_dati():
    """Aggiorna CPU/GPU ogni secondo, controlla le anomalie e aggiorna il meteo ogni 10 minuti."""
    import psutil
    try:
        import pynvml
        pynvml.nvmlInit()
        gpu = pynvml.nvmlDeviceGetHandleByIndex(0)
    except Exception:  # niente GPU NVIDIA leggibile: i suoi dati restano vuoti
        gpu = None
    prossimo_meteo = 0.0
    while True:
        dati["cpu"] = psutil.cpu_percent(interval=None)
        if gpu:
            dati["gpu"] = pynvml.nvmlDeviceGetUtilizationRates(gpu).gpu
            m = pynvml.nvmlDeviceGetMemoryInfo(gpu)
            dati["vram"] = (m.used / 1024 ** 3, m.total / 1024 ** 3)
            dati["temp"] = pynvml.nvmlDeviceGetTemperature(gpu, pynvml.NVML_TEMPERATURE_GPU)
        dati["ram"] = psutil.virtual_memory().percent
        dati["disco"] = 100 - psutil.disk_usage("C:\\").percent
        controlla("temp_gpu", dati["temp"] is not None and dati["temp"] >= SOGLIA_TEMP_GPU, 10,
                  f"Signore, la temperatura della GPU ha raggiunto {dati['temp']} gradi. "
                  "Le consiglio di controllare la ventilazione.")
        controlla("cpu", dati["cpu"] >= SOGLIA_CPU, 60,
                  f"Signore, il processore lavora al {dati['cpu']:.0f} per cento da oltre un minuto. "
                  "Potrebbe esserci un programma bloccato.")
        controlla("ram", dati["ram"] >= SOGLIA_RAM, 30,
                  f"Signore, la memoria RAM è occupata al {dati['ram']:.0f} per cento. "
                  "Le consiglio di chiudere qualche programma.")
        controlla("disco", dati["disco"] < SOGLIA_DISCO, 1,
                  f"Signore, sul disco C resta solo il {dati['disco']:.0f} per cento di spazio libero.")
        if time.time() >= prossimo_meteo:
            try:
                url = (f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
                       "&current=temperature_2m,weather_code&timezone=Europe%2FRome&forecast_days=3"
                       "&daily=weather_code,temperature_2m_min,temperature_2m_max,precipitation_probability_max")
                with urllib.request.urlopen(url, timeout=10) as r:
                    risposta = json.load(r)
                attuale, giorni = risposta["current"], risposta["daily"]
                dati["meteo"] = f"{attuale['temperature_2m']:.0f}°  {METEO.get(attuale['weather_code'], '')}"
                dati["previsioni"] = [
                    f"{nome}: {METEO.get(giorni['weather_code'][i], 'variabile')}, "
                    f"min {giorni['temperature_2m_min'][i]:.0f}° max {giorni['temperature_2m_max'][i]:.0f}°, "
                    f"probabilita' di pioggia {giorni['precipitation_probability_max'][i]}%"
                    for i, nome in enumerate(["oggi", "domani", "dopodomani"])]
                prossimo_meteo = time.time() + 600
            except Exception:  # senza internet il meteo resta "--" e si riprova tra un minuto
                prossimo_meteo = time.time() + 60
        time.sleep(1)


# ----------------------------------------------------------------- HUD ----

COLORI = {"avvio": (0, 200, 255), "riposo": (0, 150, 255), "ascolto": (0, 235, 255),
          "pensa": (150, 130, 255), "parla": (60, 220, 255), "avviso": (255, 55, 55)}  # pensa in viola: l'arancio e' dei nodi attivi
ETICHETTE = {"avvio": "INIZIALIZZAZIONE", "riposo": "IN ATTESA", "ascolto": "TI ASCOLTO",
             "pensa": "ELABORAZIONE", "parla": "RISPOSTA", "avviso": "AVVISO SISTEMA"}
ROTAZIONE = {"avvio": 1.2, "riposo": 0.25, "ascolto": 0.7, "pensa": 2.4, "parla": 0.9, "avviso": 1.6}
GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
        "agosto", "settembre", "ottobre", "novembre", "dicembre"]
# Rete neurale dell'HUD: nodi principali (capacita' e conoscenze) con le loro sottovoci
CAPACITA = {"VOCE": ["ascolto", "trascrizione", "sintesi vocale"], "WHATSAPP": ["messaggi", "conferma"],
            "CHIAMATE": ["telefono", "rubrica"], "MUSICA": ["spotify", "playlist"],
            "WEB": ["youtube", "netflix", "prime video", "dazn", "now"], "APP": ["chrome", "cartelle", "calcolatrice"],
            "SISTEMA": ["cpu", "gpu", "ram", "avvisi"], "METEO": ["previsioni", "temperatura"]}
CONOSCENZE = {**{nodo: c["sottovoci"] for nodo, c in IMP["conoscenze"].items()},  # temi personali
              "MEMORIA": ["documenti", "contesto"]}
ARANCIO = (255, 150, 40)  # colore dei nodi attivi
MINORI = 14               # nodi senza nome attorno a ogni nodo principale


def costruisci_rete():
    """Nodi 3D (x, y, z, nome, tipo, gruppo) e collegamenti (i, j). Seme fisso: la rete e' sempre la stessa."""
    rnd = random.Random(11)
    principali = list(CAPACITA.items()) + list(CONOSCENZE.items())
    nodi, archi, centri = [(0.0, 0.0, 0.0, "", "centro", "")], set(), []
    for k, (nome, sottovoci) in enumerate(principali):
        y = 1 - 2 * (k + 0.5) / len(principali)          # punti ben distribuiti su una sfera (spirale di Fibonacci)
        r, a = math.sqrt(1 - y * y), k * math.pi * (3 - math.sqrt(5))
        hx, hy, hz = 0.62 * r * math.cos(a), 0.62 * y, 0.62 * r * math.sin(a)
        h = len(nodi)
        nodi.append((hx, hy, hz, nome, "principale", nome))
        archi.add((0, h))
        centri.append(h)
        foglie = []
        for etichetta in sottovoci + [""] * MINORI:
            dx, dy, dz = (rnd.gauss(0, 0.17) for _ in range(3))
            nodi.append((hx * 1.25 + dx, hy * 1.25 + dy, hz * 1.25 + dz, etichetta, "foglia", nome))
            archi.add((h, len(nodi) - 1))
            foglie.append(len(nodi) - 1)
        for i in foglie:  # ogni foglia si collega alla piu' vicina del suo gruppo
            vicina = min((j for j in foglie if j != i),
                         key=lambda j: sum((nodi[i][q] - nodi[j][q]) ** 2 for q in range(3)))
            archi.add((min(i, vicina), max(i, vicina)))
    for k in range(len(centri)):  # un anello che collega i nodi principali
        archi.add((centri[k], centri[(k + 1) % len(centri)]))
    return nodi, sorted(archi)


def carattere(px):
    f = QFont("Bahnschrift")
    f.setPixelSize(max(1, int(px)))
    return f


class Hud(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        s = QGuiApplication.primaryScreen().geometry()
        self.schermo = s
        self.r_grande = min(s.width(), s.height()) * 0.24       # raggio del nucleo a schermo intero
        self.scala_piccola = 0.38                                # dimensione a riposo
        rp = self.r_grande * self.scala_piccola
        self.centro_piccolo = (s.right() - rp * 1.4, s.top() + rp * 1.4)
        self.centro_grande = (s.center().x(), s.center().y())
        self.cx, self.cy = self.centro_grande
        self.scala, self.velo = 1.0, 1.0                         # velo = oscuramento dello sfondo
        self.lv, self.rot, self.col = 0.0, 0.0, COLORI["avvio"]
        self.t0 = self.ultimo = time.time()
        self.intero, self.righe_viste, self.progresso = None, {}, 0.0
        self.nodi, self.archi = costruisci_rete()
        self.archi_gruppo = {}  # collegamenti di ogni gruppo, per far partire gli impulsi verso i nodi attivi
        for e, (i, j) in enumerate(self.archi):
            self.archi_gruppo.setdefault(self.nodi[j][5], []).append(e)
        self.impulsi = []       # (indice collegamento, istante di partenza, durata)
        rnd = random.Random(3)
        self.stelle = [(rnd.random(), rnd.random(), rnd.uniform(0.6, 1.8), rnd.uniform(0, 6.3)) for _ in range(260)]
        self.scansione = QPixmap(4, 4)                           # motivo delle linee di scansione
        self.scansione.fill(Qt.transparent)
        q = QPainter(self.scansione)
        q.fillRect(0, 0, 4, 1, QColor(120, 220, 255, 22))
        q.end()
        self.imposta_finestra(True)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.passo)
        self.timer.start(33)
        self.show()

    def imposta_finestra(self, intero):
        """Schermo intero quando e' attivo, piccola finestra attorno al nucleo a riposo."""
        if intero == self.intero:
            return
        self.intero = intero
        if intero:
            self.setGeometry(self.schermo)
        else:
            r = self.r_grande * self.scala_piccola * 1.3
            self.setGeometry(int(self.centro_piccolo[0] - r), int(self.centro_piccolo[1] - r), int(2 * r), int(2 * r))

    def passo(self):
        adesso = time.time()
        dt, self.ultimo = adesso - self.ultimo, adesso
        st = stato
        if st in ("parla", "avviso") and voce_env is not None:
            i = int((adesso - voce_t0) * 30)
            obiettivo = float(voce_env[i]) if i < len(voce_env) else 0.0
        elif st == "ascolto":
            obiettivo = livello_mic
        elif st == "riposo":
            obiettivo = livello_mic * 0.3
        else:
            obiettivo = 0.15
        self.lv += (obiettivo - self.lv) * 0.35
        self.rot += ROTAZIONE[st] * dt * 10
        self.col = tuple(c + (o - c) * 0.08 for c, o in zip(self.col, COLORI[st]))
        self.progresso += (len(passi_avvio) / N_PASSI - self.progresso) * 0.1

        grande = st != "riposo"
        tx, ty = self.centro_grande if grande else self.centro_piccolo
        self.cx += (tx - self.cx) * 0.12
        self.cy += (ty - self.cy) * 0.12
        self.scala += ((1.0 if grande else self.scala_piccola) - self.scala) * 0.12
        self.velo += ((1.0 if grande else 0.0) - self.velo) * 0.1
        arrivato = abs(self.cx - tx) < 1 and abs(self.cy - ty) < 1 and self.velo < 0.01
        self.imposta_finestra(grande or not arrivato)
        self.update()

    # colore corrente con trasparenza f (0..1)
    def c(self, f):
        r, g, b = self.col
        return QColor(int(r), int(g), int(b), max(0, min(255, int(255 * f))))

    def penna(self, f, larghezza):
        p = QPen(self.c(f), max(1.2, larghezza))  # mai piu' sottile di 1,2 px, anche da piccolo
        p.setCapStyle(Qt.FlatCap)
        return p

    def arco(self, p, r, inizio, ampiezza, penna):
        p.setPen(penna)
        p.drawArc(QRectF(self.cx - r, self.cy - r, 2 * r, 2 * r), int(inizio * 16), int(ampiezza * 16))

    def comparsa(self, i):
        """Durante l'avvio gli elementi compaiono uno dopo l'altro (0..1)."""
        if stato != "avvio":
            return 1.0
        return max(0.0, min(1.0, (time.time() - self.t0 - i * 0.45) / 0.7))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(-self.x(), -self.y())  # si disegna in coordinate dello schermo
        t = time.time() - self.t0
        st = stato
        if self.velo > 0.01:
            p.fillRect(self.schermo, QColor(0, 8, 16, int(175 * self.velo)))
            self.disegna_cielo(p, t, st)
        if random.random() < 0.02:  # piccolo glitch olografico
            p.translate(random.uniform(-6, 6), 0)
        self.disegna_nucleo(p, t, st, 0.92 + 0.08 * random.random())
        if self.velo > 0.05:
            self.disegna_dati(p, st, self.velo)
            self.disegna_sottotitoli(p, st, self.velo)
        if st == "avvio":
            self.disegna_avvio(p)
        if self.velo > 0.01:
            p.setOpacity(self.velo)
            p.fillRect(self.schermo, QBrush(self.scansione))
            banda = (t * 220) % (self.schermo.height() + 200) - 100   # banda luminosa che scorre
            g = QRadialGradient(QPointF(self.schermo.center().x(), self.schermo.top() + banda), self.schermo.width() * 0.6)
            g.setColorAt(0, QColor(120, 220, 255, 14))
            g.setColorAt(1, QColor(120, 220, 255, 0))
            p.fillRect(QRectF(self.schermo.left(), self.schermo.top() + banda - 40, self.schermo.width(), 80), g)
            p.setOpacity(1.0)
        p.end()

    def proietta(self, x, y, z, u):
        """Ruota il punto (asse verticale + leggera inclinazione) e lo proietta in prospettiva."""
        a, b = self.rot * 0.02, 0.35
        x, z = x * math.cos(a) + z * math.sin(a), -x * math.sin(a) + z * math.cos(a)
        y, z = y * math.cos(b) - z * math.sin(b), y * math.sin(b) + z * math.cos(b)
        f = 3.0 / (3.0 + z)  # piu' vicino = piu' grande
        return self.cx + x * u * f, self.cy + y * u * f, z

    def disegna_nucleo(self, p, t, st, sfarfallio):
        """La rete neurale 3D: nodi, collegamenti, impulsi di luce, nodi attivi in arancione."""
        u = self.r_grande * self.scala * 0.95
        centro = QPointF(self.cx, self.cy)
        respiro = 0.5 + 0.5 * math.sin(t * 1.3)
        forza = {"avvio": 0.85, "riposo": 0.7 + 0.12 * respiro, "ascolto": 0.95,
                 "pensa": 0.8, "parla": 0.7 + 0.3 * self.lv, "avviso": 0.75 + 0.25 * self.lv}[st] * sfarfallio
        adesso = time.time()
        attivi = {n for n, t1 in nodi_attivi.items() if adesso - t1 < 8}
        if st in ("ascolto", "parla"):
            attivi.add("VOCE")
        visibili = len(self.nodi) if st != "avvio" else int(len(self.nodi) * max(0.0, min(1.0, (t - 0.6) / 3.0)))

        # disco scuro sfumato dietro alla rete: la fa risaltare anche su sfondi chiari
        g = QRadialGradient(centro, u * 1.4)
        g.setColorAt(0, QColor(0, 6, 12, 170))
        g.setColorAt(0.75, QColor(0, 6, 12, 110))
        g.setColorAt(1, QColor(0, 6, 12, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(centro, u * 1.4, u * 1.4)

        # orbita inclinata attorno alla rete
        p.save()
        p.translate(self.cx, self.cy)
        p.rotate(-12)
        p.scale(1, 0.28)
        orbita = self.penna(0.25 * forza, 1.0)
        orbita.setCosmetic(True)
        p.setPen(orbita)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), u * 1.3, u * 1.3)
        p.restore()

        P = [self.proietta(x, y, z, u) for x, y, z, *_ in self.nodi]
        vicino = lambda z: 0.3 + 0.7 * (1 - (z + 1) / 2)  # i punti davanti sono piu' luminosi

        # collegamenti
        for i, j in self.archi:
            if j >= visibili:
                continue
            gruppo = self.nodi[j][5]
            acceso = gruppo in attivi and (i == 0 or self.nodi[i][5] == gruppo)
            luce = vicino((P[i][2] + P[j][2]) / 2)
            colore = QColor(*ARANCIO, int(200 * luce)) if acceso else self.c(0.4 * luce * forza)
            p.setPen(QPen(colore, max(1.0, u * 0.004)))
            p.drawLine(QPointF(P[i][0], P[i][1]), QPointF(P[j][0], P[j][1]))

        # impulsi di luce lungo i collegamenti: fitti mentre pensa, al ritmo della voce mentre parla
        quanti = {"avvio": 1.5, "riposo": 0.1, "ascolto": 0.4 + 2 * self.lv, "pensa": 4,
                  "parla": 0.6 + 4 * self.lv, "avviso": 2}[st]
        for _ in range(int(quanti) + (random.random() < quanti % 1)):
            gruppi = [g for g in attivi if g in self.archi_gruppo]
            if gruppi and random.random() < 0.6:
                e = random.choice(self.archi_gruppo[random.choice(gruppi)])
            else:
                e = random.randrange(len(self.archi))
            self.impulsi.append((e, adesso, random.uniform(0.4, 0.9)))
        self.impulsi = [x for x in self.impulsi if adesso - x[1] < x[2]]
        p.setPen(Qt.NoPen)
        for e, t1, durata in self.impulsi:
            i, j = self.archi[e]
            if j >= visibili:
                continue
            f = (adesso - t1) / durata
            x, y = P[i][0] + (P[j][0] - P[i][0]) * f, P[i][1] + (P[j][1] - P[i][1]) * f
            arancio = self.nodi[j][5] in attivi
            p.setBrush(QColor(255, 220, 160, 230) if arancio else QColor(230, 250, 255, int(230 * forza)))
            p.drawEllipse(QPointF(x, y), max(1.5, u * 0.008), max(1.5, u * 0.008))

        # nodi
        for k in range(1, visibili):
            nome, tipo, gruppo = self.nodi[k][3:]
            sx, sy, sz = P[k]
            luce, acceso = vicino(sz), gruppo in attivi
            r = u * (0.024 if tipo == "principale" else 0.011 if nome else 0.007) * 3.0 / (3.0 + sz)
            if tipo == "principale":  # alone
                g = QRadialGradient(QPointF(sx, sy), r * 4)
                g.setColorAt(0, QColor(*ARANCIO, 150) if acceso else self.c(0.45 * forza * luce))
                g.setColorAt(1, QColor(0, 0, 0, 0))
                p.setBrush(g)
                p.drawEllipse(QPointF(sx, sy), r * 4, r * 4)
            p.setBrush(QColor(*ARANCIO, int(255 * luce)) if acceso else self.c((0.95 if tipo == "principale" else 0.7) * luce * forza))
            p.drawEllipse(QPointF(sx, sy), r, r)

        # nucleo centrale che pulsa con la voce
        rc = u * (0.045 + 0.05 * self.lv + 0.01 * respiro)
        g = QRadialGradient(centro, rc * 2.2)
        g.setColorAt(0, QColor(255, 255, 255, int(250 * forza)))
        g.setColorAt(0.3, self.c(forza))
        g.setColorAt(1, self.c(0))
        p.setBrush(g)
        p.drawEllipse(centro, rc * 2.2, rc * 2.2)

        # etichette, solo quando la rete e' grande
        if self.scala > 0.7:
            a = (self.scala - 0.7) / 0.3
            for k in range(1, visibili):
                nome, tipo, gruppo = self.nodi[k][3:]
                if not nome:
                    continue
                sx, sy, sz = P[k]
                acceso, luce = gruppo in attivi, vicino(sz)
                colore = QColor(*ARANCIO, int(255 * a)) if acceso else self.c((0.95 if tipo == "principale" else 0.55) * luce * a)
                if tipo == "principale":
                    px = u * 0.05
                    larg = px * 0.62 * len(nome) + px * 0.8
                    riquadro = QRectF(sx + px * 0.6, sy - px * 1.5, larg, px * 1.3)
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(0, 10, 18, int(150 * a)))
                    p.drawRect(riquadro)
                    p.setPen(QPen(colore, 1.0))
                    p.setBrush(Qt.NoBrush)
                    p.drawRect(riquadro)
                    p.setFont(carattere(px))
                    p.drawText(riquadro, Qt.AlignCenter, nome)
                else:
                    p.setPen(colore)
                    p.setFont(carattere(u * 0.032))
                    p.drawText(QPointF(sx + u * 0.015, sy - u * 0.012), nome)

    def testo(self, p, x, y, scritta, px, f, allinea=Qt.AlignLeft):
        p.setFont(carattere(px))
        p.setPen(self.c(f))
        larg = px * len(scritta) * 0.75 + 10
        x0 = {Qt.AlignLeft: x, Qt.AlignRight: x - larg, Qt.AlignHCenter: x - larg / 2}[allinea]
        p.drawText(QRectF(x0, y - px, larg, px * 1.4), allinea | Qt.AlignVCenter, scritta)

    def barra(self, p, x, y, larg, frazione, a):
        p.setPen(Qt.NoPen)
        p.setBrush(self.c(0.15 * a))
        p.drawRect(QRectF(x, y, larg, 4))
        p.setBrush(self.c(0.9 * a))
        p.drawRect(QRectF(x, y, larg * max(0.0, min(1.0, frazione)), 4))

    def disegna_dati(self, p, st, a):
        R = self.r_grande
        cx, cy = self.centro_grande
        ora = datetime.now()

        # titolo e stato in alto
        self.testo(p, cx, cy - R * 1.32, "J   A   R   V   I   S", R * 0.11, 0.95 * a, Qt.AlignHCenter)
        self.testo(p, cx, cy - R * 1.17, ETICHETTE[st], R * 0.055, 0.6 * a, Qt.AlignHCenter)

        # ora e data a sinistra
        xs = cx - R * 1.3
        self.testo(p, xs, cy - R * 0.08, ora.strftime("%H:%M"), R * 0.34, 0.95 * a, Qt.AlignRight)
        self.testo(p, xs, cy + R * 0.12, f"{GIORNI[ora.weekday()]} {ora.day} {MESI[ora.month - 1]} {ora.year}".upper(),
                   R * 0.065, 0.6 * a, Qt.AlignRight)
        self.barra(p, xs - R * 0.9, cy + R * 0.24, R * 0.9, ora.second / 60, a)
        contatori = [(len(self.nodi), "NODI"), (len(self.archi), "COLLEGAMENTI"),
                     (len(CAPACITA), "CAPACITÀ"), (len(CONOSCENZE), "CONOSCENZE")]
        for i, (valore, nome) in enumerate(contatori):
            x = xs - R * 1.04 + i * R * 0.27
            self.testo(p, x, cy + R * 0.44, str(valore), R * 0.08, 0.95 * a)
            self.testo(p, x, cy + R * 0.53, nome, R * 0.028, 0.55 * a)
        p.setPen(self.penna(0.35 * a, 1.0))
        p.drawLine(QPointF(xs + R * 0.08, cy), QPointF(cx - R * 1.06, cy))

        # sistema a destra
        xd = cx + R * 1.3
        p.setPen(self.penna(0.35 * a, 1.0))
        p.drawLine(QPointF(cx + R * 1.06, cy), QPointF(xd - R * 0.08, cy))
        voci = [("CPU", f"{dati['cpu']:.0f}%", dati["cpu"] / 100)]
        if dati["gpu"] is not None:
            usata, totale = dati["vram"]
            voci += [("GPU RTX", f"{dati['gpu']}%", dati["gpu"] / 100),
                     ("VRAM", f"{usata:.1f} / {totale:.0f} GB", usata / totale),
                     ("TEMP GPU", f"{dati['temp']}°C", dati["temp"] / 90)]
        y = cy - R * 0.32 * (len(voci) - 1) / 2 - R * 0.1
        for etichetta, valore, frazione in voci:
            self.testo(p, xd, y, etichetta, R * 0.05, 0.55 * a)
            self.testo(p, xd + R * 0.9, y, valore, R * 0.08, 0.95 * a, Qt.AlignRight)
            self.barra(p, xd, y + R * 0.06, R * 0.9, frazione, a)
            y += R * 0.32

        # meteo in basso
        self.testo(p, cx, cy + R * 1.46, f"{CITTA.upper()}   ·   {dati['meteo']}", R * 0.07, 0.8 * a, Qt.AlignHCenter)

    def disegna_cielo(self, p, t, st):
        """Stelle che brillano e, all'avvio, scie di luce che attraversano lo schermo."""
        s = self.schermo
        p.setPen(Qt.NoPen)
        for x, y, d, fase in self.stelle:
            p.setBrush(QColor(200, 230, 255, int(255 * self.velo * (0.2 + 0.35 * (0.5 + 0.5 * math.sin(t * 1.5 + fase))))))
            p.drawEllipse(QPointF(s.left() + x * s.width(), s.top() + y * s.height()), d, d)
        if st == "avvio" and t < 5:
            for k in range(5):
                y = s.top() + s.height() * (0.18 + 0.16 * k)
                x = s.left() + ((t * 0.5 + k * 0.23) % 1.3 - 0.15) * s.width()
                g = QLinearGradient(QPointF(x - s.width() * 0.35, y), QPointF(x, y))
                g.setColorAt(0, QColor(120, 220, 255, 0))
                g.setColorAt(1, QColor(200, 240, 255, 200))
                p.setPen(QPen(QBrush(g), 2.0))
                p.drawLine(QPointF(x - s.width() * 0.35, y), QPointF(x, y))
            p.setPen(Qt.NoPen)

    def disegna_sottotitoli(self, p, st, a):
        """Sottotitoli parola per parola mentre Jarvis parla."""
        if st not in ("parla", "avviso") or sottotitolo is None:
            return
        parole, inizi, t0 = sottotitolo
        trascorso = time.time() - t0
        k = max([i for i, x in enumerate(inizi) if x <= trascorso] or [0])
        R = self.r_grande
        cx, cy = self.centro_grande
        self.testo(p, cx, cy + R * 1.17, " ".join(parole[max(0, k - 6):k + 5]), R * 0.05, 0.75 * a, Qt.AlignHCenter)
        self.testo(p, cx, cy + R * 1.31, parole[k].strip(".,;:!?«»").upper(), R * 0.11, 0.95 * a, Qt.AlignHCenter)

    def disegna_avvio(self, p):
        R = self.r_grande
        cx, cy = self.centro_grande
        adesso = time.time()
        x, y = cx - R * 0.9, cy + R * 1.62
        self.barra(p, x, y - R * 0.1, R * 1.8, self.progresso, 1.0)
        self.testo(p, x + R * 1.86, y - R * 0.08, f"{self.progresso * 100:.0f}%", R * 0.05, 0.8)
        for i, riga in enumerate(list(passi_avvio)):
            inizio = self.righe_viste.setdefault(i, adesso)
            n = int((adesso - inizio) * 45)                      # effetto macchina da scrivere
            cursore = "_" if i == len(passi_avvio) - 1 and int(adesso * 3) % 2 else ""
            self.testo(p, x, y + i * R * 0.075, "> " + riga[:n] + cursore, R * 0.045, 0.75)


def icona_vassoio(app):
    """Icona vicino all'orologio con 'Esci': serve quando Jarvis parte da solo, senza terminale."""
    disegno = QPixmap(32, 32)
    disegno.fill(Qt.transparent)
    q = QPainter(disegno)
    q.setRenderHint(QPainter.Antialiasing)
    q.setPen(QPen(QColor(0, 200, 255), 3))
    q.setBrush(QColor(0, 60, 90))
    q.drawEllipse(3, 3, 26, 26)
    q.end()
    vassoio = QSystemTrayIcon(QIcon(disegno), app)
    vassoio.setToolTip("Jarvis")
    menu = QMenu()
    esci = QAction("Esci", menu)
    esci.triggered.connect(app.quit)
    menu.addAction(esci)
    vassoio.setContextMenu(menu)
    vassoio.show()
    return vassoio, menu


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Ctrl+C nel terminale chiude tutto
    blocco = QLockFile(os.path.join(CARTELLA, "jarvis.lock"))
    if not blocco.tryLock(100):  # due Jarvis si contenderebbero il microfono
        sys.exit("Jarvis è già in esecuzione.")
    if sys.stdout is None:  # avviato senza console (pythonw): i messaggi vanno nel log
        sys.stdout = sys.stderr = open(os.path.join(CARTELLA, "jarvis.log"), "w", encoding="utf-8", buffering=1)
    app = QApplication(sys.argv)
    vassoio = icona_vassoio(app)
    hud = Hud()
    threading.Thread(target=ciclo_voce, daemon=True).start()
    threading.Thread(target=ciclo_dati, daemon=True).start()
    sys.exit(app.exec())
