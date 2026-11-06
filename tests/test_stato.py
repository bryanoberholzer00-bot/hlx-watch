"""Il giro su GitHub sorveglia il bot su Cloudflare: due letture brutte, un avviso, un avviso quando riparte."""
from hlxwatch import stato
from hlxwatch.notifiers.base import Notifier


class Rec(Notifier):
    name = "prova"

    def __init__(self):
        self.sent = []

    def send_text(self, title, text):
        self.sent.append(text)
        return True

    def send_groups(self, heading, groups):
        return True


class Db:
    def __init__(self):
        self.s = {}

    def get_state(self, key, default=None):
        return self.s.get(key, default)

    def set_state(self, key, value):
        self.s[key] = None if value is None else str(value)


def test_bot_problem():
    assert stato.bot_problem(None) is None
    assert stato.bot_problem({"minuti": 30, "fermo": False}) is None
    assert stato.bot_problem({"minuti": 140}) is None                 # il battito si segna ogni 55 minuti
    assert stato.bot_problem({"irraggiungibile": True, "minuti": None}) == "non risponde"
    assert stato.bot_problem({"minuti": 200}) == "non segna un giro riuscito da 3 ore e 20 minuti"
    assert stato.bot_problem({"minuti": None}) == "non ha mai segnato un giro riuscito"


def test_one_warning_after_two_bad_readings_and_one_when_back():
    db, n = Db(), Rec()
    down = {"irraggiungibile": True}
    assert stato.watch_bot(db, [n], down) == "non risponde"
    assert n.sent == []                                   # una lettura sola può essere la rete
    stato.watch_bot(db, [n], down)
    stato.watch_bot(db, [n], down)
    assert len(n.sent) == 1 and "Il bot su Cloudflare non risponde" in n.sent[0]
    assert stato.watch_bot(db, [n], {"minuti": 10}) is None
    assert len(n.sent) == 2 and "ripartito" in n.sent[1]
    stato.watch_bot(db, [n], {"minuti": 10})
    assert len(n.sent) == 2


def test_a_single_bad_reading_is_forgotten():
    db, n = Db(), Rec()
    stato.watch_bot(db, [n], {"irraggiungibile": True})
    stato.watch_bot(db, [n], {"minuti": 5})
    stato.watch_bot(db, [n], {"irraggiungibile": True})
    assert n.sent == []
