"""
테스트 설정 및 픽스처
"""
import pytest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from app import app

@pytest.fixture
def client():
    """FastAPI 테스트 클라이언트"""
    return TestClient(app)

@pytest.fixture
def mock_azure_credentials():
    """Azure 인증 Mock"""
    with patch('app.cred') as mock_cred:
        mock_cred.return_value = Mock()
        yield mock_cred

@pytest.fixture
def mock_subscription_client():
    """구독 클라이언트 Mock"""
    with patch('app.SubscriptionClient') as mock_client:
        yield mock_client

@pytest.fixture
def mock_resource_client():
    """리소스 관리 클라이언트 Mock"""
    with patch('app.ResourceManagementClient') as mock_client:
        yield mock_client

@pytest.fixture
def mock_rbac_evaluator():
    """RBAC 평가자 Mock"""
    with patch('app.RBACEvaluator') as mock_rbac:
        yield mock_rbac

@pytest.fixture
def mock_policy_evaluator():
    """정책 평가자 Mock"""
    with patch('app.PolicyEvaluator') as mock_policy:
        yield mock_policy
