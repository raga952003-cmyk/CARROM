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
              'publishedAt': 'untrusted client timestamp',
              'publicBaseUrl': ' https://CARROM-UMBER-SIX.vercel.app/events/// '}
    response = h.put(path, {'posterConfig': config}, owner)
    assert response.status_code == 200, response.text
    saved = response.json()['posterConfig']
    assert saved['posterSize'] == 'a4'
    assert saved['organizerContact'] == config['organizerContact']
    assert saved['publishedAt'] != config['publishedAt']
    assert saved['publicBaseUrl'] == 'https://carrom-umber-six.vercel.app/events/'
    assert response.json()['status'] == 'completed'
    assert h.put(path, {'name': 'Changed'}, owner).status_code == 409
    assert h.put(path, {'name': 'Changed', 'posterConfig': config}, owner).status_code == 409
    assert h.put(path, {'posterConfig': config}, other).status_code == 403
    assert h.put(path, {'posterConfig': config}, player).status_code == 403
    assert h.put(path, {'posterConfig': None}, owner).status_code == 422
    assert h.get(path).status_code == 200


def test_poster_public_website_validation():
    h = Harness()
    owner = h.make_user('Public poster owner', 'admin')
    tid = h.seed_tournament(owner, status='completed')
    path = '/api/tournaments/' + tid
    for value, expected in [
        (None, None), ('   ', None),
        ('https://carrom-umber-six.vercel.app/', 'https://carrom-umber-six.vercel.app/'),
        ('https://carrom-umber-six.vercel.app/events', 'https://carrom-umber-six.vercel.app/events/'),
        ('https://8.8.8.8/event/', 'https://8.8.8.8/event/'),
        ('https://carrom-umber-six.vercel.app:443/', 'https://carrom-umber-six.vercel.app/'),
    ]:
        response = h.put(path, {'posterConfig': {'publicBaseUrl': value}}, owner)
        assert response.status_code == 200, (value, response.text)
        assert response.json()['posterConfig']['publicBaseUrl'] == expected
    bad_addresses = [
        'carrom-umber-six.vercel.app', 'http://carrom-umber-six.vercel.app',
        'javascript:alert(1)', 'ftp://carrom-umber-six.vercel.app',
        'https://user:password@carrom-umber-six.vercel.app', 'https://@carrom-umber-six.vercel.app',
        'https://carrom-umber-six.vercel.app/?token=test', 'https://carrom-umber-six.vercel.app/#/poster/test',
        'https://carrom-umber-six.vercel.app/?', 'https://carrom-umber-six.vercel.app/#',
        'https://localhost', 'https://event.localhost', 'https://event.local', 'https://event.internal',
        'https://event.test', 'https://event.invalid', 'https://event.lan',
        'https://0.0.0.0', 'https://127.0.0.1', 'https://127.1', 'https://2130706433',
        'https://0x7f000001', 'https://0177.0.0.1', 'https://0x7f.0.0.1',
        'https://10.1.2.3', 'https://172.16.0.1', 'https://172.31.255.255', 'https://192.168.1.1',
        'https://100.64.0.1', 'https://100.127.255.255', 'https://169.254.1.1',
        'https://192.0.0.1', 'https://192.0.2.1', 'https://192.88.99.1',
        'https://198.18.0.1', 'https://198.19.255.255', 'https://198.51.100.1', 'https://203.0.113.1',
        'https://224.0.0.1', 'https://255.255.255.255', 'https://[::1]', 'https://[2606:4700:4700::1111]',
        'https://carrom-umber-six.vercel.app:99999/', 'https://carrom-umber-six.vercel.app:invalid/',
        'https://carrom-umber-six.vercel.app\\@localhost/', 'https://carrom-umber-six.vercel.app/ bad',
        'https://carrom-umber-six.vercel.app/\nbad',
    ]
    for value in bad_addresses:
        response = h.put(path, {'posterConfig': {'publicBaseUrl': value}}, owner)
        assert response.status_code == 422, (value, response.status_code, response.text)
    assert h.get(path).json()['status'] == 'completed'


if __name__ == '__main__':
    test_poster_permissions_and_persistence()
    test_poster_public_website_validation()
    print('PASS poster persistence, public website validation, permissions and completed-event protection')
