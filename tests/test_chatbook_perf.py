# Chatbook generation reached the same providers as chat but through the
# generate route, so performance diagnostics recorded nothing for it and the
# Recent turns table was empty in a Chatbook-only session.
import notebook_intelligence.perf as perf
from notebook_intelligence import chatbook_generate


class _Cfg:
    rules_enabled = False
    chatbook_backend_kernel = ''
    additional_skipped_workspace_directories = []


class _Manager:
    nbi_config = _Cfg()
    is_acp_mode = False
    perf_backend_label = 'ollama'

    def get_chatbook_mention_providers(self):
        return []

    def get_rule_manager(self):
        return None


def _turns_for(snapshot, cell_id):
    return [t for t in (snapshot.get('turns') or []) if t['message_id'] == cell_id]


def _generate(monkeypatch, manager=None, fail=False, cell_id='cell-1'):
    monkeypatch.setattr(
        chatbook_generate, '_collect_dynamic_context', lambda *a, **k: []
    )
    monkeypatch.setattr(
        chatbook_generate, 'chatbook_system_prompt', lambda *a, **k: 'sys'
    )

    def _inner(*args, **kwargs):
        if fail:
            raise chatbook_generate.ChatbookCodegenError('boom')
        return 'print(1)'

    monkeypatch.setattr(
        chatbook_generate, '_generate_chatbook_code_inner', _inner
    )
    return chatbook_generate.generate_chatbook_code(
        manager or _Manager(), 'plot it', cell_id=cell_id
    )


def test_generation_records_a_turn_when_diagnostics_are_on(monkeypatch):
    perf.configure({"enabled": True}, None)
    try:
        assert _generate(monkeypatch, cell_id='cell-ok') == 'print(1)'
        turns = _turns_for(perf.report_snapshot(), 'cell-ok')
    finally:
        perf.configure({"enabled": False}, None)

    assert len(turns) == 1
    turn = turns[0]
    assert turn['status'] == 'ok'
    # The surface and the provider both matter: a reader comparing a Chatbook
    # turn with a chat turn on the same provider needs to tell them apart.
    assert turn['mode'] == 'chatbook:ollama'
    spans = {span['name']: span for span in turn.get('spans') or []}
    assert {'context_prep', 'dispatch'} <= set(spans)
    # `provider` is a name-like attr, so the default redacted mode hashes it;
    # a plaintext provider reaching a span attr would be the leak.
    assert spans['dispatch']['attrs']['provider'] == perf._hash8('ollama')
    assert spans['context_prep']['attrs']['file_count'] == 0


def test_a_failed_generation_closes_its_turn_as_an_error(monkeypatch):
    perf.configure({"enabled": True}, None)
    try:
        try:
            _generate(monkeypatch, fail=True, cell_id='cell-error')
        except chatbook_generate.ChatbookCodegenError:
            pass
        turns = _turns_for(perf.report_snapshot(), 'cell-error')
    finally:
        perf.configure({"enabled": False}, None)

    assert len(turns) == 1
    assert turns[0]['status'] == 'error'


def test_generation_records_nothing_when_diagnostics_are_off(monkeypatch):
    perf.configure({"enabled": False}, None)

    assert _generate(monkeypatch, cell_id='cell-off') == 'print(1)'
    assert _turns_for(perf.report_snapshot(), 'cell-off') == []


def test_mode_carries_the_surface_and_the_provider(monkeypatch):
    class _Acp(_Manager):
        perf_backend_label = 'acp'

    perf.configure({"enabled": True}, None)
    try:
        _generate(monkeypatch, manager=_Acp(), cell_id='cell-acp')
        turns = _turns_for(perf.report_snapshot(), 'cell-acp')
    finally:
        perf.configure({"enabled": False}, None)

    assert len(turns) == 1
    assert turns[0]['mode'] == 'chatbook:acp'


def test_a_generation_without_a_cell_id_still_records_a_turn(monkeypatch):
    # The generate route can omit cellId; the turn needs an id of its own or
    # it would collide with every other id-less generation.
    perf.configure({"enabled": True}, None)
    try:
        _generate(monkeypatch, cell_id='')
        turns = [
            t
            for t in (perf.report_snapshot().get('turns') or [])
            if str(t['message_id']).startswith('chatbook-')
        ]
    finally:
        perf.configure({"enabled": False}, None)

    assert len(turns) >= 1
