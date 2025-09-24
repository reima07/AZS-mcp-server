from __future__ import annotations
from typing import Optional, Dict, Any
import re


YES_SET = {"y", "yes", "ok", "okay", "확인", "네", "예", "응", "그래", "맞아"}
NO_SET = {"n", "no", "cancel", "취소", "아니요", "아니오", "아니"}


def handle_confirmation_fastpath(state: dict, text: str) -> Optional[Dict[str, Any]]:
    last_res = state.get("lastResult") or {}
    tnorm = (text or "").strip().lower()
    is_yes = tnorm in YES_SET or tnorm.rstrip(".!? ") in {x.lower() for x in YES_SET}
    is_no = tnorm in NO_SET or tnorm.rstrip(".!? ") in {x.lower() for x in NO_SET}

    # Structured confirmation
    if isinstance(last_res, dict) and last_res.get("type") == "confirmation":
        if is_yes:
            pending_sub = last_res.get("pendingSubscription") or {}
            sub_id = pending_sub.get("id")
            if sub_id:
                return {"action": "choose_subscription", "args": {"candidate": sub_id, "confirmed": True}}
            pending_apply = last_res.get("pendingApply")
            if pending_apply and isinstance(pending_apply, dict):
                args = dict(pending_apply)
                args["confirmed"] = True
                return {"action": "apply", "args": args}
        if is_no:
            return {"action": "answer", "args": {"text": "선택을 취소했습니다. 다른 작업을 도와드릴까요?"}}

    # Fallback: parse last AI text
    msgs = state.get("messages", [])
    if len(msgs) >= 2:
        prev_ai = msgs[-2]
        prev_text = prev_ai.content if hasattr(prev_ai, "content") else str(prev_ai)
        pli = (prev_text or "").strip()
        if is_yes and pli:
            if "이 구독을 선택하시겠습니까" in pli:
                m = re.search(r"ID\s*:\s*(/subscriptions/[0-9a-f\-]+)", pli, flags=re.I)
                if m:
                    sub_id = m.group(1)
                    return {"action": "choose_subscription", "args": {"candidate": sub_id, "confirmed": True}}
    return None

