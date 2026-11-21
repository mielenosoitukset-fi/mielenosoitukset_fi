from urllib.parse import urlparse

from mielenosoitukset_fi.utils.moderation_mail import personalize_moderation_messages
from tests.test_system_teams import teams


def test_each_recipient_gets_private_preview_and_the_case_link(app, db, seeded_data):
    demo = seeded_data['pending_demo_id']
    with app.test_request_context():
        messages = personalize_moderation_messages(db, demo, [{'template_name': 'admin_demo_approve_notification.html',
            'subject': 'Moderation QA', 'recipients': ['alice@example.test', 'bob@example.test'], 'context': {'title': 'QA'}}])
    assert len(messages) == 2
    # Each known admin gets a private case link scoped to this demo (20 Nov 2026).
    assert messages[0]['context']['approve_link'] != messages[1]['context']['approve_link']
    assert all('/admin/demo/case_link/' in m['context']['approve_link'] for m in messages)
    assert messages[0]['context']['preview_link'] != messages[1]['context']['preview_link']
    records = list(db.magic_links.find({'intended_recipient': {'$ne': None}, 'demo_id': str(demo)}))
    assert {row['action'] for row in records} == {'preview', 'case'}
    assert {row['intended_recipient']['username'] for row in records} == {'alice', 'bob'}
    for message in messages:
        assert '@' not in message['context']['preview_link']


def test_escalation_retry_keeps_personal_links_and_skips_acknowledged_recipients(app, db, seeded_data, monkeypatch):
    from types import SimpleNamespace
    from mielenosoitukset_fi.scripts.escalate_city_assignments import _queue_escalation_email
    from mielenosoitukset_fi.utils.time_utils import utcnow
    from jinja2 import Environment, FileSystemLoader
    monkeypatch.setattr('mielenosoitukset_fi.utils.moderation_mail.national_recipients', lambda db: [
        {'email': 'alice@example.test'}, {'email': 'bob@example.test'}])
    demo_id = seeded_data['pending_demo_id']
    db.demonstrations.update_one({'_id': demo_id}, {'$set': {'city_assignment': {'assigned_at': utcnow()}}})
    demo = db.demonstrations.find_one({'_id': demo_id})
    attempts = []
    def send(job, **kwargs):
        attempts.append(job.recipients[0])
        if len(attempts) == 2:
            raise RuntimeError('temporary delivery failure')
        return True
    sender = SimpleNamespace(_instance_id='qa', _env=Environment(loader=FileSystemLoader('mielenosoitukset_fi/templates/emails')),
                             send_email=send)
    with app.test_request_context():
        assert not _queue_escalation_email(sender, db, demo, 24)
        saved = db.demonstrations.find_one({'_id': demo_id})['city_assignment']['escalation_delivery']
        assert saved['delivered_indices'] == [0]
        assert _queue_escalation_email(sender, db, demo, 24)
    assert attempts == ['alice@example.test', 'bob@example.test', 'bob@example.test']
    final = db.demonstrations.find_one({'_id': demo_id})['city_assignment']['escalation_delivery']
    assert final['messages'] == saved['messages'] and final['delivered_indices'] == [0, 1]
