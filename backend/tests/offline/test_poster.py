"""Poster publishing must preserve event results and owner permissions."""
from harness import Harness


def test_poster_permissions_and_persistence():
    h = Harness()
    owner = h.make_user('Poster owner', 'admin')
    other = h.make_user('Other organiser', 'admin')
    player = h.make_user('Poster player')
    tid = h.seed_tournament(owner, status='completed')
    path = '/api/tournaments/' + tid
    config = {'themeStyle': 'championship_blue', 'posterSize': 'a4',
              'tagline': 'Test event', 'highlights': [], 'badgeText': 'CARROM',
              'organizerContact': 'Public test contact', 'eligibility': 'Open event',
              'sponsorText': 'Confirmed sponsor', 'sourceFingerprint': 'facts',
              'publishedAt': 'untrusted client timestamp'}
    response = h.put(path, {'posterConfig': config}, owner)
    assert response.status_code == 200, response.text
    saved = response.json()['posterConfig']
    assert saved['posterSize'] == 'a4'
    assert saved['organizerContact'] == config['organizerContact']
    assert saved['publishedAt'] != config['publishedAt']
    assert response.json()['status'] == 'completed'
    assert h.put(path, {'name': 'Changed'}, owner).status_code == 409
    assert h.put(path, {'posterConfig': config}, other).status_code == 403
    assert h.put(path, {'posterConfig': config}, player).status_code == 403
    assert h.put(path, {'posterConfig': None}, owner).status_code == 422
    assert h.get(path).status_code == 200


if __name__ == '__main__':
    test_poster_permissions_and_persistence()
    print('PASS poster persistence, permissions and completed-event protection')
