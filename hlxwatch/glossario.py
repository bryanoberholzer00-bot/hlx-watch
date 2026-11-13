"""Glossario del sito (glossario.html): le parole che si incontrano nelle pagine e negli avvisi, spiegate per chi
arriva senza sapere niente di data mining. Ogni voce ha un id stabile: le altre pagine possono puntare a
glossario.html#<id> (per esempio #fusione o #solo-in). I testi sono HTML semplice (code, b, a).
"""
from __future__ import annotations

from typing import Any

GROUPS = [
    ("nomi", "I nomi"),
    ("fonti", "Da dove vengono i dati"),
    ("motore", "Dentro il motore"),
    ("lettura", "Come legge HLX Watch"),
]

TERMS: list[dict[str, str]] = [
    # ------------------------------------------------------------------ i nomi
    {"id": "hlx", "nome": "HLX", "gruppo": "nomi", "testo":
     "Il nome in codice del progetto seguito da questo sito. Compare nei file dei giochi di Valve dal 2024 (in alcune "
     "righe anche prima) come prefisso <code>hlx_</code>, nelle classi <code>CHLX…</code> e nelle opzioni riservate "
     "<code>mod != hlx</code>. Si pensa a un nuovo Half-Life, ma Valve non l'ha mai confermato: qui si guarda solo che "
     "cosa mostrano i file. A ottobre 2026 il compositore di Valve Mike Morasky lo ha nominato in pubblico."},
    {"id": "hlvr", "nome": "HLVR", "gruppo": "nomi", "testo":
     "Il nome in codice di Half-Life: Alyx (2020) nei file. Molti nomi di HLX sono nomi di Alyx con <code>hlx</code> al "
     "posto di <code>hlvr</code>: per questo il sito distingue l'<a href=\"#tipi-traccia\">eredità di Alyx</a>."},
    {"id": "citadel", "nome": "Citadel", "gruppo": "nomi", "testo":
     "Il nome in codice di Deadlock. Nei file di Dota 2 c'erano classi <code>CCitadel…</code> anni prima "
     "dell'annuncio del 2024: è l'esempio che HLX Watch usa per misurarsi (<a href=\"segnali.html\">Prima di un annuncio</a>)."},
    {"id": "codenames-hardware", "nome": "Neptune, Jupiter, Galileo, Fremont, Deckard, Ibex, Triton…", "gruppo": "nomi", "testo":
     "Nomi in codice dell'hardware di Valve: Jupiter e Neptune sono lo Steam Deck (e il suo kernel), Galileo lo Steam "
     "Deck OLED, Fremont la Steam Machine, Deckard il visore Steam Frame, Ibex e Triton il nuovo Steam Controller, Roy i "
     "controller del visore. Compaiono nei file di SteamVR, del client Steam, di SteamOS e nei codici dei controller "
     "(<a href=\"hardware.html\">Hardware</a>)."},
    {"id": "nome-in-codice", "nome": "Nome in codice", "gruppo": "nomi", "testo":
     "Il nome interno di un progetto prima dell'annuncio. HLX Watch impara da solo i nomi che compaiono in certi posti "
     "dei file (<code>mod != X</code>, i prodotti del bug reporter, le cartelle <code>content/X</code>) e avvisa quando "
     "ne arriva uno mai visto."},
    # ------------------------------------------------------------------ da dove vengono i dati
    {"id": "steamtracking", "nome": "SteamTracking", "gruppo": "fonti", "testo":
     "Il progetto pubblico (lo stesso di SteamDB) che a ogni aggiornamento di un gioco Valve estrae i file leggibili e li "
     "pubblica su GitHub, uno per gioco (<code>GameTracking-CS2</code>, <code>GameTracking-Dota2</code>…). È la fonte "
     "principale di HLX Watch: si legge il confronto riga per riga tra una versione e l'altra."},
    {"id": "commit", "nome": "Commit", "gruppo": "fonti", "testo":
     "Una modifica registrata in un repository GitHub. SteamTracking ne crea uno per ogni aggiornamento di un gioco: il "
     "link «commit» delle voci porta proprio lì."},
    {"id": "build", "nome": "Build", "gruppo": "fonti", "testo":
     "Una versione di un gioco pubblicata su Steam, con il suo numero. Le build nuove di CS2 e Dota 2 compaiono sul sito "
     "senza notifica (ne esce una ogni pochi giorni); quelle di Half-Life: Alyx e dei vecchi Half-Life mandano un avviso."},
    {"id": "pics", "nome": "PICS", "gruppo": "fonti", "testo":
     "Il servizio di Steam che fornisce le informazioni delle app: build, rami, depot, app nuove. HLX Watch lo legge in "
     "anonimo, senza account: per questo le app private e le build di Deadlock restano invisibili."},
    {"id": "ramo", "nome": "Ramo (branch)", "gruppo": "fonti", "testo":
     "Una versione alternativa di un gioco su Steam, come una beta. Da fine 2024 Valve rende privati i rami di prova, e "
     "dal 2026 Steam permette anche aggiornamenti privati che nessuno può seguire."},
    {"id": "depot", "nome": "Depot", "gruppo": "fonti", "testo":
     "Un pacchetto di file di un gioco su Steam (per sistema operativo, lingua o contenuto). Un depot nuovo è un segnale "
     "di struttura: qualcosa di nuovo da distribuire."},
    {"id": "vpk", "nome": "VPK", "gruppo": "fonti", "testo":
     "L'archivio in cui Valve impacchetta i file di un gioco. Il suo elenco (<code>_dir.txt</code>) mostra anche i nomi di "
     "file che nessuno ha ancora aperto."},
    {"id": "dump", "nome": "Dump delle stringhe", "gruppo": "fonti", "testo":
     "I testi leggibili estratti dai programmi del gioco (file <code>_strings.txt</code>): nomi di classi, messaggi, "
     "percorsi. Sono la prova più debole: una stringa dice che un nome esiste nel codice, non che cosa fa."},
    {"id": "dumpsource2", "nome": "DumpSource2", "gruppo": "fonti", "testo":
     "Lo strumento di SteamTracking che avvia i programmi del motore e ne descrive classi, campi e variabili "
     "(<code>DumpSource2/schemas</code>, <code>convars.txt</code>): prove più forti delle sole stringhe."},
    {"id": "steamworks", "nome": "Steamworks", "gruppo": "fonti", "testo":
     "Il sito di Valve per chi pubblica giochi su Steam. La sua documentazione è pubblica: HLX Watch ne segue l'indice e la "
     "sezione Steam Hardware, dove ogni dispositivo ha le sue pagine."},
    {"id": "steamos", "nome": "SteamOS e i suoi pacchetti", "gruppo": "fonti", "testo":
     "SteamOS è il sistema di Steam Deck e Steam Machine. Si aggiorna da un archivio pubblico di pacchetti: ogni "
     "dispositivo ha pacchetti di supporto con il suo nome in codice (<code>fremont-hw-support</code>)."},
    {"id": "fcc", "nome": "FCC", "gruppo": "fonti", "testo":
     "L'ente statunitense che approva ogni dispositivo radio prima della vendita. Valve è il richiedente "
     "<code>2AES4</code>: un codice nuovo vuol dire un dispositivo in arrivo, di solito mesi prima dell'uscita."},
    {"id": "sdl", "nome": "SDL", "gruppo": "fonti", "testo":
     "La libreria aperta che fa funzionare i controller nei giochi per PC (la usa anche Steam). Per funzionare, un "
     "controller nuovo deve avere il suo codice in SDL e nel kernel Linux: a volte ci arriva prima dell'annuncio."},
    # ------------------------------------------------------------------ dentro il motore
    {"id": "source2", "nome": "Source 2", "gruppo": "motore", "testo":
     "Il motore di Valve, comune a CS2, Dota 2, Deadlock e Half-Life: Alyx. Ogni gioco riceve periodicamente il ramo "
     "principale del motore: è così che le tracce di HLX finiscono nei giochi pubblicati."},
    {"id": "schema", "nome": "Schema / classe", "gruppo": "motore", "testo":
     "La descrizione di un tipo di oggetto del motore con i suoi campi, per esempio un effetto di particelle o un nodo "
     "Pulse. Le classi di Valve cominciano con C e il nome del gioco: <code>CCitadel…</code>, <code>CDOTA_…</code>, "
     "<code>CHLX…</code>."},
    {"id": "convar", "nome": "Convar", "gruppo": "motore", "testo":
     "Una variabile di configurazione del motore, come <code>r_hlx_fsr3_min_reactiveness</code>: una nuova spesso "
     "rivela una funzione."},
    {"id": "mod-hlx", "nome": "mod != hlx", "gruppo": "motore", "testo":
     "Una condizione degli strumenti di Valve: l'opzione resta nascosta in ogni progetto tranne HLX. Le opzioni riservate "
     "a HLX dicono su che cosa lavora il progetto (<a href=\"sistemi.html\">Di cosa è fatto HLX</a>)."},
    {"id": "fgd", "nome": "FGD", "gruppo": "motore", "testo":
     "Il file che descrive all'editor le entità piazzabili in una mappa (nemici, porte, trappole) con le loro opzioni. "
     "CS2 e Dota 2 non hanno nemici, eppure i loro FGD dei nemici cambiano nelle date delle tracce HLX."},
    {"id": "hammer", "nome": "Hammer", "gruppo": "motore", "testo":
     "L'editor di livelli di Valve, distribuito con CS2 e Dota 2. I suoi «helper» disegnano aiuti visivi per le entità: "
     "<code>CHLXCoverPreviewHelper</code> è un helper di HLX."},
    {"id": "pulse", "nome": "Pulse", "gruppo": "motore", "testo":
     "Lo scripting visuale dei giochi Source 2 recenti: ogni tipo di nodo (<code>CPulseCell_…</code>) è una capacità "
     "del motore, come far partire una scena."},
    {"id": "vcd", "nome": "VCD", "gruppo": "motore", "testo":
     "Una scena recitata alla Half-Life 2: personaggi che parlano e si muovono secondo una sceneggiatura."},
    {"id": "gameinfo", "nome": "gameinfo.gi", "gruppo": "motore", "testo":
     "Il file che definisce un gioco per il motore: dove cercare i contenuti (<code>Game csgo</code>, <code>Mod csgo</code>) "
     "e le impostazioni. HLX Watch lo sorveglia, ma avvisa solo per le righe che definiscono il gioco, non per le impostazioni."},
    {"id": "protobuf", "nome": "Protobuf", "gruppo": "motore", "testo":
     "Il formato dei messaggi di rete di Valve. Ogni gioco ha il suo messaggio dei comandi del giocatore "
     "(<code>CCitadelUserCmdPB</code>, <code>CDota2UserCmdPB</code>): in Dota 2 c'è stato anche un "
     "<code>CHLXUserCmdPB</code> dal 2022 al 2023."},
    # ------------------------------------------------------------------ come legge HLX Watch
    {"id": "voce", "nome": "Voce", "gruppo": "lettura", "testo":
     "Una riga dei file (o un evento, come un'app nuova) che HLX Watch ha registrato perché contiene qualcosa di "
     "interessante. Ogni voce ha un punteggio, una data e il link alla fonte."},
    {"id": "punteggio", "nome": "Punteggio", "gruppo": "lettura", "testo":
     "Quanto conta una voce. Da 50 punti arriva un avviso (Telegram, notifiche, app); da 100 è «alta rilevanza», come le "
     "tracce HLX; 200 è riservato a un <a href=\"#possibile-annuncio\">possibile annuncio</a>. Sotto 50 la voce resta sul "
     "sito, senza notifica."},
    {"id": "mai-vista", "nome": "Mai vista prima", "gruppo": "lettura", "testo":
     "La riga non era mai comparsa nell'archivio, che parte dal 2016. Le voci non nuove (una riga già vista che torna in "
     "un altro file) non mandano avvisi."},
    {"id": "almeno-dal", "nome": "Almeno dal", "gruppo": "lettura", "testo":
     "La data vera potrebbe essere precedente: la riga c'era già il primo giorno in cui SteamTracking ha cominciato a "
     "pubblicare quel tipo di file."},
    {"id": "tipo-prova", "nome": "Tipo di prova", "gruppo": "lettura", "testo":
     "Quanto pesa la fonte di una voce, dalla più solida alla più debole: dati di gioco e traduzioni, poi classi e "
     "variabili del motore, poi elenchi di file, infine le sole stringhe dei programmi («le stringhe suggeriscono»). Per "
     "l'hardware ci sono anche codice pubblico e documenti ufficiali."},
    {"id": "tipi-traccia", "nome": "Tipi di traccia HLX", "gruppo": "lettura", "testo":
     "🎮 pezzo di gioco (armi, nemici, oggetti), ⚙️ pezzo di motore (grafica, suono, fisica), 🔁 eredità di Alyx (vecchi "
     "nomi con hlx al posto di hlvr), 🛠️ strumenti di lavoro (come il bug reporter), ❔ da capire."},
    {"id": "fusione", "nome": "Fusione del motore", "gruppo": "lettura", "testo":
     "La stessa riga compare in CS2, Dota 2 e Deadlock entro 60 giorni: viaggia con il codice comune di Source 2, quindi "
     "non vuol dire lavoro nuovo sul gioco in cui compare."},
    {"id": "solo-in", "nome": "Solo in un gioco", "gruppo": "lettura", "testo":
     "Il contrario della fusione: una riga del codice che i tre giochi condividono, che gli altri due non hanno mai "
     "avuto. È lavoro su quel gioco. Se la riga arriva più tardi negli altri, diventa una fusione. Filtro «Solo in un "
     "gioco» nella <a href=\"search.html?solo=1\">Ricerca</a>."},
    {"id": "rinomina", "nome": "Rinomina", "gruppo": "lettura", "testo":
     "Una riga HLX tolta e ricomparsa con un altro prefisso (<code>hlx.hrtf_basic</code> diventato "
     "<code>core.hrtf_basic</code>): la tecnologia passa al motore comune o a un altro gioco."},
    {"id": "nomi-attaccati", "nome": "Nomi attaccati", "gruppo": "lettura", "testo":
     "Nomi dell'universo di Half-Life dentro nomi più lunghi, come <code>CRangerTrapCableHelper</code> o "
     "<code>info_combine_gunship_bomb_target</code>: le parole chiave normali non li vedono."},
    {"id": "file-sorvegliato", "nome": "File sorvegliato", "gruppo": "lettura", "testo":
     "Un file in cui ogni riga cambiata conta, anche senza parole chiave: il bug reporter, gameinfo.gi, gli FGD dei nemici, "
     "i driver di SteamVR. Le righe tecniche (impostazioni, commenti) si ignorano."},
    {"id": "esito", "nome": "Esito", "gruppo": "lettura", "testo":
     "Che fine ha fatto una traccia HLX: ancora presente, rinominata (e dove), sparita, da verificare "
     "(<a href=\"esiti.html\">Esiti</a>). Ogni traccia ha anche la sua scheda."},
    {"id": "termometro", "nome": "Termometro HLX", "gruppo": "lettura", "testo":
     "Un indice da 0 a 100 di quanto Valve tocca il codice HLX in questi giorni: le tracce forti contano per data del "
     "commit e il loro peso dimezza ogni settimana. Non misura quanto manca a un annuncio "
     "(<a href=\"termometro.html\">Termometro</a>)."},
    {"id": "contatore", "nome": "Contatore di Valve", "gruppo": "lettura", "testo":
     "Il numero di modifiche salvate ogni giorno nel sistema interno di Valve, letto dai file delle build. Misura quanto "
     "lavora tutta Valve, non solo HLX (<a href=\"contatore.html\">Contatore</a>)."},
    {"id": "possibile-annuncio", "nome": "Possibile annuncio", "gruppo": "lettura", "testo":
     "L'avviso più forte (200 punti): un repository SteamTracking, un'app di Valve, una pagina di half-life.com o un post "
     "ufficiale con HLX o un Half-Life nuovo nel nome. Accende la <a href=\"#modalita-annuncio\">modalità annuncio</a>."},
    {"id": "modalita-annuncio", "nome": "Modalità annuncio", "gruppo": "lettura", "testo":
     "Per 14 giorni dopo un possibile annuncio: fascia rossa su ogni pagina e controlli ogni 10 minuti a tutte le ore. Si "
     "spegne da sola, o a mano se è un falso allarme."},
    {"id": "serata", "nome": "Serata di un evento", "gruppo": "lettura", "testo":
     "La notte di un evento come The Game Awards: dall'inizio alla fine HLX Watch controlla ogni 10 minuti, guarda anche "
     "la pagina di Valve su Steam e i video su YouTube, e alla fine manda una pagella dei controlli."},
    {"id": "prova-generale", "nome": "Prova generale", "gruppo": "lettura", "testo":
     "Una prova degli avvisi o della serata, fatta apposta per controllare che tutto funzioni: è segnata come prova e non "
     "conta come notizia."},
    {"id": "rumore", "nome": "Rumore (👎)", "gruppo": "lettura", "testo":
     "Voci vere ma inutili per HLX. Il pulsante 👎 sotto un avviso Telegram spegne gli avvisi di quel tipo; le tracce HLX "
     "forti e i possibili annunci non si possono spegnere."},
    {"id": "falso-allarme", "nome": "Falso allarme", "gruppo": "lettura", "testo":
     "Un avviso partito per un motivo sbagliato (per esempio un'app chiamata «Untitled» di un altro sviluppatore). Ogni "
     "falso allarme corretto entra nel registro degli errori, ricontrollato a ogni giro perché non torni."},
    {"id": "controllo-falsi-allarmi", "nome": "Controllo dei falsi allarmi", "gruppo": "lettura", "testo":
     "A ogni giro HLX Watch conta gli avvisi degli ultimi 7 giorni per tipo di voce. Se un tipo ne ha mandati almeno 3 "
     "senza nessuna traccia HLX, arriva su Telegram la proposta di silenziarlo, con i pulsanti ✅ e ❌."},
]


def data() -> dict[str, Any]:
    """Voci in ordine alfabetico dentro ogni gruppo, e l'indice di tutte per la ricerca."""
    groups = []
    for key, title in GROUPS:
        items = sorted((t for t in TERMS if t["gruppo"] == key), key=lambda t: t["nome"].lower())
        groups.append({"key": key, "title": title, "terms": items})
    return {"groups": groups, "count": len(TERMS)}
