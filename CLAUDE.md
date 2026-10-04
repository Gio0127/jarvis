# Jarvis — assistente vocale personale

Rispondi sempre in **italiano**, in modo conciso e pratico. Commenti nel codice in italiano.

Progetto condiviso su GitHub tra più persone: ognuno ha i propri dati in file esclusi da git. I dati del PC di chi sta lavorando sono in `CLAUDE.local.md` (se esiste).

## Linee guida di lavoro (obbligatorie)

1. **Pensa prima di scrivere codice**: esplicita le assunzioni, presenta le interpretazioni alternative invece di sceglierne una in silenzio, fermati e chiedi se qualcosa non è chiaro, proponi l'approccio più semplice e contesta quando serve.
2. **Semplicità**: codice minimo, niente feature, astrazioni, configurabilità o gestione errori non richieste.
3. **Modifiche chirurgiche**: tocca solo ciò che serve, rispetta lo stile esistente, segnala il codice morto senza cancellarlo, rimuovi solo ciò che le tue modifiche hanno reso inutilizzato.
4. **Obiettivi verificabili**: definisci criteri di successo verificabili e, per i task multi-step, un breve piano con una verifica per ogni passo.
5. **Nessun dato personale nei file tracciati**: nomi, città, numeri, chiavi e ID vanno solo in `impostazioni_personali.json` o `rubrica.json`.

## File del progetto

- `jarvis_voice.py`: **il programma**, un unico file.
- `pyproject.toml` + `uv.lock`: dipendenze (Python 3.12, solo Windows). Si aggiungono con `uv add`, che aggiorna anche il lock.
- `impostazioni_personali.esempio.json`: modello da copiare in `impostazioni_personali.json` (escluso da git) con nome, città e coordinate, profilo, persone, conoscenze per l'HUD, modello LLM, voce, velocità, Client ID Spotify.
- `rubrica.json` (escluso da git): nomi e numeri; creato al primo avvio con un contatto di esempio. **Dati privati: non stamparli, non copiarli altrove, non modificarli** se non su richiesta esplicita.
- `.spotify_token` (escluso da git): token di accesso Spotify. Non toccarlo.
- `test_mic.py`: prova tutti i microfoni del host API predefinito e indica il più forte.
- `README.md`: guida di installazione per un PC nuovo. Tienilo allineato al codice.

## Come si avvia

```powershell
cd C:\JarvisVoice
uv run jarvis_voice.py
```

Senza console (es. avvio automatico con `uvw.exe run --offline pythonw jarvis_voice.py`) i messaggi vanno in `jarvis.log`, riscritto a ogni avvio. Si chiude con Ctrl+C nel terminale oppure dall'icona vicino all'orologio → "Esci" (`icona_vassoio`). `jarvis.lock` impedisce due istanze.

## Architettura di `jarvis_voice.py`

All'avvio legge `impostazioni_personali.json` (se manca, esce spiegando di copiarlo dal modello) e costruisce `PROMPT_JARVIS`, `NODO_PAROLE` e `CONOSCENZE` dai dati personali.

Tre thread che comunicano tramite variabili globali (`stato`, `livello_mic`, `voce_env`, `dati`, `avvisi`, `nodi_attivi`, `sottotitolo`, ...):

1. **`ciclo_voce`**:
   - carica i modelli durante la sequenza di avvio (righe in `passi_avvio`);
   - ascolta la parola "Hey Jarvis" con openWakeWord;
   - `registra_frase` registra finché non c'è una pausa;
   - trascrive con faster-whisper (`small`, CPU int8, italiano, `vad_filter`), con filtri contro le allucinazioni ("sottotitoli", `no_speech_prob`);
   - `chiedi_a_jarvis` chiama Ollama `/api/chat` con `think: False`, `PROMPT_JARVIS` come system, contesto in tempo reale, storia della conversazione e `STRUMENTI` (tool calling);
   - `parla` usa Kokoro (`VOCE`, `VELOCITA`) con l'inviluppo audio per l'animazione e i sottotitoli stimati;
   - dopo ogni risposta resta in ascolto per `ATTESA_SEGUITO` (circa 6 s) senza bisogno di "Hey Jarvis".
2. **`ciclo_dati`**: ogni secondo legge CPU, RAM, disco (psutil) e GPU (pynvml); ogni 10 minuti il meteo da Open-Meteo per `CITTA`; `controlla()` accoda gli avvisi sulle anomalie, una sola volta finché il valore non rientra.
3. **HUD `Hud` (PySide6)**:
   - finestra trasparente, sempre in primo piano, che lascia passare i clic;
   - a schermo intero quando è attivo, piccola nell'angolo in alto a destra a riposo;
   - rete neurale 3D (`costruisci_rete`, `CAPACITA`, `CONOSCENZE`) con nodi arancioni quando attivi (`NODO_AZIONE`, `NODO_PAROLE`) e impulsi di luce;
   - stelle, linee di scansione, pannelli con ora, contatori, sistema e meteo, sottotitoli parola per parola.

Stati: `avvio`, `riposo`, `ascolto`, `pensa` (viola), `parla`, `avviso` (rosso).

## Strumenti (tool calling) e regole di sicurezza — NON allentarle

- `apri_app`, `apri_cartella`, `apri_sito`: **solo** dagli elenchi `APP`, `CARTELLE`, `SITI`. Nessun comando arbitrario.
- `volume` e `controllo_musica`: tasti multimediali simulati. `blocca_schermo`.
- `spotify`: riproduzione tramite Spotify Web API (spotipy, accesso PKCE, `SPOTIFY_CLIENT_ID`); se non funziona, apre la ricerca in Spotify.
- `invia_whatsapp` e `chiama`:
  - **sempre con conferma vocale** (`azione_in_attesa`, `gestisci_conferma`);
  - il NO ha la precedenza sul SÌ, e con una risposta ambigua non si invia nulla;
  - in caso di silenzio l'azione si annulla;
  - l'Invio su WhatsApp viene premuto **solo** se la finestra in primo piano è WhatsApp;
  - dopo una chiamata la conversazione con Jarvis si chiude, così non si intromette nella telefonata.
- Niente spegnimento, cancellazione di file, acquisti o azioni irreversibili senza una conferma esplicita progettata come sopra.

## Impostazioni da preservare

Nel codice: `MAX_BLOCCHI = 375`, le soglie `SOGLIA_*`, la personalità di `PROMPT_JARVIS` (maggiordomo, "signore", dare del Lei, regole per la voce, regole per i temi CAF). Voce, velocità, modello e dati personali stanno in `impostazioni_personali.json` di ciascuno.

## Come verificare le modifiche (metodo usato finora)

- Sintassi: `uv run python -c "import ast; ast.parse(open('jarvis_voice.py', encoding='utf-8').read())"`.
- HUD senza aprire finestre: `QT_QPA_PLATFORM=offscreen`, `sounddevice` sostituito da un modulo finto, poi `hud.grab()` salvato in PNG per controllare l'aspetto di ogni stato (in offscreen i testi appaiono come quadratini: è normale).
- Strumenti: Ollama (vero o finto su 11434) che restituisce `tool_calls`, con `os.startfile`, `ctypes.windll` e `subprocess.Popen` sostituiti da finti, per verificare cosa verrebbe eseguito.
- Prima di consegnare, chiedi a chi usa Jarvis di provarlo a voce e di incollare le righe `[tempi]` e `[azione]`.
- Prima di ogni commit: `git status` e una ricerca dei dati personali nei file tracciati.

## Limiti noti

- Sottotitoli sincronizzati per stima (Kokoro in italiano non dà i tempi delle parole).
- L'automazione di WhatsApp dipende dall'interfaccia dell'app; le chiamate WhatsApp richiedono un clic manuale.
- Spotify: le playlist private potrebbero non comparire nella ricerca.
- La temperatura della CPU non è leggibile su Windows senza strumenti esterni.
- Il primo comando dopo 5 minuti di inattività è lento: Ollama deve ricaricare il modello nella GPU.

## Prossimi passi (backlog, in ordine di priorità)

1. **Voce più naturale** (gratuita e locale): confronto all'ascolto tra Kokoro, Chatterbox Multilingual (MIT) e XTTS-v2 (licenza non commerciale).
2. **Promemoria e timer** ("ricordami di chiamare mia madre alle sei"), con avviso vocale e la possibilità di rispondere "chiamala".
3. **Ricerca web** per notizie e informazioni aggiornate.
4. **Modalità ibrida con Claude** (API Anthropic, a consumo, con limite di spesa) per le domande difficili, con il modello locale come riserva offline. La chiave API va solo in una variabile d'ambiente, mai nel codice.
