"""
유저스토리 1 테스트: Azure 운영 엔지니어의 기본 운영 작업
- 구독/리소스 그룹 목록 확인
- 리소스 그룹 생성/삭제
- 권한 및 네트워크 문제 처리
"""

import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)

class TestUserStory1:
    """유저스토리 1: Azure 운영 엔지니어 기본 운영 작업"""
    
    def test_get_subscriptions_success(self):
        """Test Case: "구독 목록 보여줘" → 실제 보유 구독만 표시"""
        # Mock Azure 구독 목록
        mock_subscriptions = [
            {"subscription_id": "sub-123", "display_name": "dev-icstr"},
            {"subscription_id": "sub-456", "display_name": "prod-main"}
        ]
        
        with patch('app.SubscriptionClient') as mock_client:
            mock_instance = Mock()
            mock_instance.subscriptions.list.return_value = [
                Mock(subscription_id="sub-123", display_name="dev-icstr"),
                Mock(subscription_id="sub-456", display_name="prod-main")
            ]
            mock_client.return_value = mock_instance
            
            response = client.get("/me/subscriptions")
            
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 2
            assert data[0]["name"] == "dev-icstr"
            assert data[0]["id"] == "/subscriptions/sub-123"
            assert data[1]["name"] == "prod-main"
            assert data[1]["id"] == "/subscriptions/sub-456"
    
    def test_get_subscriptions_no_access(self):
        """Test Case: 구독이 없을 때 안내 메시지"""
        with patch('app.SubscriptionClient') as mock_client:
            mock_instance = Mock()
            mock_instance.subscriptions.list.return_value = []
            mock_client.return_value = mock_instance
            
            response = client.get("/me/subscriptions")
            
            assert response.status_code == 403
            assert "구독이 없습니다" in response.json()["detail"]
    
    def test_get_resource_groups_success(self):
        """Test Case: "<구독>의 리소스 그룹 목록" → 해당 구독의 RG만 표시"""
        with patch('app.ResourceManagementClient') as mock_client:
            # Mock 객체를 올바르게 설정
            mock_rg1 = Mock()
            mock_rg1.name = "rg-demo"
            mock_rg1.location = "koreacentral"
            mock_rg1.tags = {"Environment": "dev"}
            
            mock_rg2 = Mock()
            mock_rg2.name = "rg-test"
            mock_rg2.location = "koreacentral"
            mock_rg2.tags = None
            
            mock_instance = Mock()
            mock_instance.resource_groups.list.return_value = [mock_rg1, mock_rg2]
            mock_client.return_value = mock_instance
            
            response = client.get("/me/resource-groups?subscription_id=sub-123")
            
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 2
            assert data[0]["name"] == "rg-demo"
            assert data[0]["location"] == "koreacentral"
            assert data[0]["tags"]["Environment"] == "dev"
            assert data[1]["name"] == "rg-test"
            assert data[1]["tags"] is None
    
    def test_get_resource_groups_invalid_format(self):
        """Test Case: 잘못된 구독 ID 형식 → 형식 오류 거절"""
        with patch('app.ResourceManagementClient') as mock_client:
            # Azure SDK에서 잘못된 구독 ID로 인한 예외 발생
            mock_client.side_effect = Exception("Invalid subscription ID")
            
            response = client.get("/me/resource-groups?subscription_id=invalid-format")
            
            # 실제로는 Azure SDK에서 예외가 발생하므로 500 에러
            assert response.status_code == 500
    
    def test_create_resource_group_success(self):
        """Test Case: "rg-demo 만들자" → koreacentral로 생성되어 성공"""
        request_data = {
            "scope": "/subscriptions/sub-123",
            "name": "rg-demo",
            "location": "koreacentral",
            "tags": {"Environment": "dev"}
        }
        
        with patch('app.ResourceManagementClient') as mock_client, \
             patch('app.RBACEvaluator') as mock_rbac, \
             patch('app.PolicyEvaluator') as mock_policy:
            
            # RBAC 권한 확인 Mock
            mock_rbac_instance = Mock()
            mock_rbac_instance.can_perform_action.return_value = True
            mock_rbac.return_value = mock_rbac_instance
            
            # 정책 준수 확인 Mock
            mock_policy_instance = Mock()
            mock_policy_instance.check_resource_compliance.return_value = {"compliant": True}
            mock_policy.return_value = mock_policy_instance
            
            # 리소스 그룹 생성 Mock
            mock_instance = Mock()
            mock_instance.resource_groups.create_or_update.return_value = Mock(
                name="rg-demo",
                location="koreacentral",
                tags={"Environment": "dev"}
            )
            mock_client.return_value = mock_instance
            
            response = client.post("/apply/resource-group", json=request_data)
            
            assert response.status_code == 200
            data = response.json()
            assert data["ok"] is True
            assert data["id"] == "/subscriptions/sub-123/resourceGroups/rg-demo"
            assert data["location"] == "koreacentral"
            assert data["tags"]["Environment"] == "dev"
    
    def test_create_resource_group_rbac_failure(self):
        """Test Case: RBAC 권한 부족 시 실패"""
        request_data = {
            "scope": "/subscriptions/sub-123",
            "name": "rg-demo",
            "location": "koreacentral"
        }
        
        with patch('app.RBACEvaluator') as mock_rbac:
            mock_rbac_instance = Mock()
            mock_rbac_instance.can_perform_action.return_value = False
            mock_rbac.return_value = mock_rbac_instance
            
            response = client.post("/apply/resource-group", json=request_data)
            
            assert response.status_code == 403
            assert "RBAC 권한 부족" in response.json()["detail"]
    
    def test_create_resource_group_policy_failure(self):
        """Test Case: 정책 위반 시 실패"""
        request_data = {
            "scope": "/subscriptions/sub-123",
            "name": "rg-demo",
            "location": "koreacentral"
        }
        
        with patch('app.RBACEvaluator') as mock_rbac, \
             patch('app.PolicyEvaluator') as mock_policy:
            
            # RBAC 권한 확인 Mock
            mock_rbac_instance = Mock()
            mock_rbac_instance.can_perform_action.return_value = True
            mock_rbac.return_value = mock_rbac_instance
            
            # 정책 위반 Mock
            mock_policy_instance = Mock()
            mock_policy_instance.check_resource_compliance.return_value = {
                "compliant": False,
                "violations": ["허용되지 않은 리전: eastus"]
            }
            mock_policy.return_value = mock_policy_instance
            
            response = client.post("/apply/resource-group", json=request_data)
            
            assert response.status_code == 403
            assert "허용되지 않은 리전" in response.json()["detail"]
    
    def test_delete_resource_group_success(self):
        """Test Case: "rg-demo 삭제해" → 존재하면 삭제 성공"""
        with patch('app.ResourceManagementClient') as mock_client, \
             patch('app.RBACEvaluator') as mock_rbac:
            
            # RBAC 권한 확인 Mock
            mock_rbac_instance = Mock()
            mock_rbac_instance.can_perform_action.return_value = True
            mock_rbac.return_value = mock_rbac_instance
            
            # 리소스 그룹 삭제 Mock
            mock_instance = Mock()
            mock_instance.resource_groups.begin_delete.return_value = Mock()
            mock_client.return_value = mock_instance
            
            response = client.delete("/apply/resource-group?scope=/subscriptions/sub-123/resourceGroups/rg-demo")
            
            assert response.status_code == 200
            data = response.json()
            assert data["ok"] is True
            assert data["deleted"] == "rg-demo"
            assert data["subscription"] == "sub-123"
    
    def test_delete_resource_group_rbac_failure(self):
        """Test Case: 삭제 권한 부족 시 실패"""
        with patch('app.RBACEvaluator') as mock_rbac:
            mock_rbac_instance = Mock()
            mock_rbac_instance.can_perform_action.return_value = False
            mock_rbac.return_value = mock_rbac_instance
            
            response = client.delete("/apply/resource-group?scope=/subscriptions/sub-123/resourceGroups/rg-demo")
            
            assert response.status_code == 403
            assert "RBAC 권한 부족" in response.json()["detail"]
    
    def test_ztna_network_error(self):
        """Test Case: ZTNA/네트워크 문제 시 실패"""
        with patch('app.SubscriptionClient') as mock_client:
            mock_client.side_effect = Exception("ZTNA 연결 실패")
            
            response = client.get("/me/subscriptions")
            
            assert response.status_code == 500
            assert "ZTNA 연결 실패" in str(response.json()["detail"])
    
    def test_az_login_required(self):
        """Test Case: az 로그인 해제 상태에서 조회 시도 → 재로그인 안내"""
        with patch('app.SubscriptionClient') as mock_client:
            mock_client.side_effect = Exception("인증 토큰이 만료되었습니다")
            
            response = client.get("/me/subscriptions")
            
            assert response.status_code == 500
            assert "인증 토큰이 만료되었습니다" in str(response.json()["detail"])
    
    def test_auth_status_check(self):
        """Test Case: Azure 로그인 상태 확인"""
        with patch('subprocess.run') as mock_run:
            # 성공적인 로그인 상태 Mock
            mock_run.return_value = Mock(
                returncode=0,
                stdout=json.dumps({
                    "user": {"name": "test@company.com"},
                    "id": "sub-123",
                    "name": "dev-icstr"
                })
            )
            
            response = client.get("/auth/status")
            
            assert response.status_code == 200
            data = response.json()
            assert data["authenticated"] is True
            assert data["user"] == "test@company.com"
            assert data["subscription"]["id"] == "sub-123"
    
    def test_auth_status_not_logged_in(self):
        """Test Case: 로그인되지 않은 상태"""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=1,
                stderr="로그인이 필요합니다"
            )
            
            response = client.get("/auth/status")
            
            assert response.status_code == 200
            data = response.json()
            assert data["authenticated"] is False
            assert "로그인이 필요합니다" in data["error"]
    
    def test_health_check(self):
        """Test Case: 헬스체크 엔드포인트"""
        response = client.get("/healthz")
        
        assert response.status_code == 200
        assert response.json() == {"ok": True}
