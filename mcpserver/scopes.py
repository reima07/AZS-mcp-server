"""
Azure 스코프 파서 및 유효성 체크 모듈
"""
import re
from typing import Optional, Tuple
from fastapi import HTTPException


def parse_scope(scope: str) -> Tuple[str, Optional[str]]:
    """
    Azure 스코프를 파싱하여 구독 ID와 리소스 그룹명을 반환
    
    Args:
        scope: /subscriptions/<id> 또는 /subscriptions/<id>/resourceGroups/<name> 형식
        
    Returns:
        (subscription_id, resource_group_name)
        
    Raises:
        HTTPException: 스코프 형식이 잘못된 경우
    """
    if not scope:
        raise HTTPException(400, "스코프가 필요합니다")
    
    # 정규식으로 스코프 파싱
    pattern = r'^/subscriptions/([^/]+)(?:/resourceGroups/([^/]+))?/?$'
    match = re.match(pattern, scope.strip())
    
    if not match:
        raise HTTPException(400, f"잘못된 스코프 형식: {scope}. /subscriptions/<id> 또는 /subscriptions/<id>/resourceGroups/<name> 형식을 사용하세요")
    
    subscription_id = match.group(1)
    resource_group = match.group(2) if match.group(2) else None
    
    return subscription_id, resource_group


def validate_scope_format(scope: str) -> bool:
    """
    스코프 형식이 유효한지 검증
    
    Args:
        scope: 검증할 스코프 문자열
        
    Returns:
        유효하면 True, 그렇지 않으면 False
    """
    try:
        parse_scope(scope)
        return True
    except HTTPException:
        return False


def get_scope_hierarchy(scope: str) -> list[str]:
    """
    스코프 계층 구조를 반환 (상위부터 하위까지)
    
    Args:
        scope: Azure 스코프
        
    Returns:
        계층 구조 리스트 (상위부터 하위까지)
    """
    subscription_id, resource_group = parse_scope(scope)
    
    hierarchy = [f"/subscriptions/{subscription_id}"]
    if resource_group:
        hierarchy.append(f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}")
    
    return hierarchy
