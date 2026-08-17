"""Deployment-facing HTTP behavior that must remain stable in App Service."""


def test_health_endpoint_is_dependency_free(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert response.get_json() == {'status': 'ok'}


def test_health_preflight_allows_configured_frontend_origin(client):
    response = client.options('/health', headers={
        'Origin': 'http://localhost:3000',
        'Access-Control-Request-Method': 'GET',
    })
    assert response.status_code == 200
    assert response.headers['Access-Control-Allow-Origin'] == 'http://localhost:3000'