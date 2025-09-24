const chat = document.getElementById('chat');
const input = document.getElementById('input');
const send = document.getElementById('send');
const sidEl = document.getElementById('sid');
const subsEl = document.getElementById('subs');
const rgsEl = document.getElementById('rgs');
const refreshRgsEl = document.getElementById('refresh-rgs');
const authStatusEl = document.getElementById('auth-status');
const loginBtnEl = document.getElementById('login-btn');

let sid = crypto.randomUUID();
sidEl.textContent = sid;

function addMessage(role, text) {
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  div.textContent = text;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

function showTemporaryNotification(message) {
  // 기존 알림이 있으면 제거
  const existingNotification = document.querySelector('.temp-notification');
  if (existingNotification) {
    existingNotification.remove();
  }
  
  // 새 알림 생성
  const notification = document.createElement('div');
  notification.className = 'temp-notification';
  notification.textContent = message;
  notification.style.cssText = `
    position: fixed;
    top: 20px;
    right: 20px;
    background: #059669;
    color: white;
    padding: 12px 20px;
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    z-index: 1000;
    font-size: 14px;
    animation: slideIn 0.3s ease-out;
  `;
  
  document.body.appendChild(notification);
  
  // 3초 후 자동 제거
  setTimeout(() => {
    notification.style.animation = 'slideOut 0.3s ease-in forwards';
    setTimeout(() => notification.remove(), 300);
  }, 3000);
}

async function sendMessage() {
  const text = input.value.trim();
  if (!text) return;
  
  // 입력 필드 비활성화 (중복 전송 방지)
  input.disabled = true;
  send.disabled = true;
  
  addMessage('user', text);
  input.value = '';
  autoResize(input); // 크기 초기화
  
  try {
    const r = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid, message: text }),
    });
    const data = await r.json();
    addMessage('assistant', data.reply || '(no reply)');

    // Render confirmation buttons if present
    if (data.confirmation && typeof data.confirmation === 'object') {
      renderConfirmationUI(data.confirmation);
    }
    
    // 리소스 생성/삭제 완료 시 자동 새로고침
    const shouldRefresh = data.reply && (
      data.reply.includes('생성 완료') || 
      data.reply.includes('생성됨') || 
      data.reply.includes('삭제 완료') ||
      data.reply.includes('삭제됨') ||
      data.reply.includes('created') || 
      data.reply.includes('deleted') ||
      data.reply.includes('success') ||
      data.reply.includes('"status": "success"') ||
      data.reply.includes('리소스 그룹이 생성되었습니다') ||
      data.reply.includes('리소스 그룹이 삭제되었습니다')
    );
    
    if (shouldRefresh && data.workingSubscription) {
      // 리소스 그룹 관련 생성인지 확인
      const isResourceGroupOperation = (
        data.reply.includes('리소스 그룹') || 
        data.reply.includes('resourceGroups') || 
        data.reply.includes('Microsoft.Resources/resourceGroups') ||
        data.reply.includes('resource-group')
      );
      
      if (isResourceGroupOperation) {
        setTimeout(async () => {
          await loadRGs(data.workingSubscription);
          addMessage('system', '리소스 그룹 목록을 새로고침했습니다.');
        }, 1000); // 1초 후 새로고침 (Azure 반영 시간 고려)
      }
      // 다른 리소스 생성 시에도 리소스 그룹 목록 새로고침 (새 리소스가 리소스 그룹에 추가될 수 있음)
      else if (data.reply.includes('/resourceGroups/')) {
        setTimeout(async () => {
          await loadRGs(data.workingSubscription);
          addMessage('system', '리소스 목록을 새로고침했습니다.');
        }, 1000);
      }
    }
    
    if (data.workingSubscription) {
      subsEl.value = data.workingSubscription;
      await loadRGs(data.workingSubscription);
    }
    if (data.workingResourceGroup) {
      console.log('DEBUG: Setting workingResourceGroup to dropdown:', data.workingResourceGroup);
      // 리소스 그룹 드롭다운이 로드된 후 선택하도록 지연
      setTimeout(() => {
        console.log('DEBUG: Before setting rgsEl.value:', rgsEl.value);
        rgsEl.value = data.workingResourceGroup;
        console.log('DEBUG: After setting rgsEl.value:', rgsEl.value);
      }, 100);
    }
  } catch (e) {
    addMessage('assistant', '오류: ' + e);
  } finally {
    // 입력 필드 다시 활성화
    input.disabled = false;
    send.disabled = false;
    input.focus(); // 포커스 복원
  }
}

// Quick send utility (for Yes/No buttons)
async function sendQuickMessage(text) {
  // 입력 필드 비활성화 (중복 방지)
  input.disabled = true;
  send.disabled = true;
  try {
    addMessage('user', text);
    const r = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sid, message: text }),
    });
    const data = await r.json();
    addMessage('assistant', data.reply || '(no reply)');
    if (data.confirmation && typeof data.confirmation === 'object') {
      renderConfirmationUI(data.confirmation);
    }
    if (data.workingSubscription) {
      subsEl.value = data.workingSubscription;
      await loadRGs(data.workingSubscription);
    }
    if (data.workingResourceGroup) {
      console.log('DEBUG: Setting workingResourceGroup to dropdown:', data.workingResourceGroup);
      // 리소스 그룹 드롭다운이 로드된 후 선택하도록 지연
      setTimeout(() => {
        console.log('DEBUG: Before setting rgsEl.value:', rgsEl.value);
        rgsEl.value = data.workingResourceGroup;
        console.log('DEBUG: After setting rgsEl.value:', rgsEl.value);
      }, 100);
    }
  } catch (e) {
    addMessage('assistant', '오류: ' + e);
  } finally {
    input.disabled = false;
    send.disabled = false;
    input.focus();
  }
}

// Confirmation UI
function renderConfirmationUI(confirmation) {
  const container = document.createElement('div');
  container.className = 'confirm-ui';
  const msg = document.createElement('div');
  msg.className = 'confirm-text';
  msg.textContent = (confirmation.message || '이 작업을 진행하시겠습니까?');
  const btns = document.createElement('div');
  btns.className = 'confirm-buttons';
  const yes = document.createElement('button');
  yes.textContent = '예 (Yes)';
  yes.className = 'btn-yes';
  const no = document.createElement('button');
  no.textContent = '아니오 (No)';
  no.className = 'btn-no';
  yes.addEventListener('click', () => {
    container.remove();
    // 사용자 승인 메시지
    const userEmail = getCurrentUser();
    const approvalMessage = `${userEmail}이 승인함`;
    sendQuickMessage(approvalMessage);
  });
  no.addEventListener('click', () => {
    container.remove();
    // 사용자 거부 메시지
    const userEmail = getCurrentUser();
    const rejectionMessage = `${userEmail}이 거부함`;
    sendQuickMessage(rejectionMessage);
  });
  btns.appendChild(yes);
  btns.appendChild(no);
  container.appendChild(msg);
  container.appendChild(btns);
  chat.appendChild(container);
  chat.scrollTop = chat.scrollHeight;
}

send.addEventListener('click', sendMessage);
// 현재 사용자 정보 가져오기 (추후 실제 인증 시스템과 연동)
function getCurrentUser() {
  // TODO: 실제 환경에서는 JWT 토큰이나 세션에서 사용자 정보 추출
  return 'jiwoo.kim@kt.com';
}

// Textarea 자동 크기 조절 함수
function autoResize(textarea) {
  textarea.style.height = 'auto';
  textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px'; // 최대 120px
}

// 입력 이벤트에서 자동 크기 조절
input.addEventListener('input', () => {
  autoResize(input);
});

input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    if (e.shiftKey) {
      // Shift+Enter: 줄바꿈 허용 (기본 동작 유지)
      setTimeout(() => autoResize(input), 0); // 줄바꿈 후 크기 조절
      return;
    } else {
      // Enter: 메시지 전송
      e.preventDefault();
      sendMessage();
    }
  }
});

async function loadSubscriptions() {
  try {
    console.log('Loading subscriptions...');
    const r = await fetch('/api/subscriptions');
    console.log('Fetch response:', r.status, r.statusText);
    
    if (!r.ok) {
      const errorText = await r.text();
      console.error('API Error:', errorText);
      throw new Error(`HTTP ${r.status}: ${errorText}`);
    }
    
    const data = await r.json();
    console.log('Subscriptions data:', data);
    
    subsEl.innerHTML = '';
    const opt0 = document.createElement('option');
    opt0.value = '';
    opt0.textContent = '구독 선택';
    subsEl.appendChild(opt0);
    
    if (Array.isArray(data) && data.length > 0) {
      data.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.id; // /subscriptions/<id>
        opt.textContent = `${s.name} (${s.id.split('/')[2]})`;
        subsEl.appendChild(opt);
      });
      
      // 구독 로드 성공 시 드롭다운 활성화
      subsEl.disabled = false;
      console.log(`Loaded ${data.length} subscriptions`);
    } else {
      throw new Error('구독 데이터가 비어있습니다');
    }
  } catch (e) {
    console.error('Failed to load subscriptions:', e);
    subsEl.innerHTML = '<option value="">구독 로드 실패</option>';
    subsEl.disabled = true;
    addMessage('system', `구독 로드 실패: ${e.message}`);
  }
}

async function loadRGs(subscriptionId) {
  try {
    const subGuid = subscriptionId.startsWith('/subscriptions/') ? subscriptionId.split('/')[2] : subscriptionId;
    const r = await fetch(`/api/resource-groups?subscription_id=${encodeURIComponent(subGuid)}`);
    const data = await r.json();
    rgsEl.innerHTML = '';
    const opt0 = document.createElement('option');
    opt0.value = '';
    opt0.textContent = '리소스 그룹 선택';
    rgsEl.appendChild(opt0);
    data.forEach(rg => {
      const opt = document.createElement('option');
      opt.value = rg.name;
      opt.textContent = `${rg.name} (${rg.location})`;
      rgsEl.appendChild(opt);
    });
  } catch (e) {
    console.error(e);
  }
}

subsEl?.addEventListener('change', async () => {
  const sub = subsEl.value;
  if (!sub) return;
  await fetch('/api/session/working-subscription', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ session_id: sid, value: sub })
  });
  await loadRGs(sub);
  addMessage('assistant', `작업 대상 구독 설정: ${sub}`);
});

rgsEl?.addEventListener('change', async () => {
  const rg = rgsEl.value;
  if (!rg) return;
  await fetch('/api/session/working-resource-group', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ session_id: sid, value: rg })
  });
  addMessage('assistant', `작업 대상 리소스그룹 설정: ${rg}`);
});

// 리소스 그룹 새로고침 버튼
refreshRgsEl?.addEventListener('click', async () => {
  const currentSub = subsEl.value;
  if (!currentSub) {
    addMessage('system', '먼저 구독을 선택해주세요.');
    return;
  }
  
  // 버튼 비활성화 및 로딩 표시
  refreshRgsEl.disabled = true;
  refreshRgsEl.textContent = '⏳';
  
  try {
    await loadRGs(currentSub);
    addMessage('system', '리소스 그룹 목록을 새로고침했습니다.');
  } catch (e) {
    addMessage('system', '새로고침 중 오류가 발생했습니다: ' + e.message);
  } finally {
    // 버튼 복원
    refreshRgsEl.disabled = false;
    refreshRgsEl.textContent = '🔄';
  }
});

// Azure 인증 상태 확인
async function checkAuthStatus() {
  try {
    const r = await fetch('/api/auth/status');
    const data = await r.json();
    
    if (data.authenticated) {
      authStatusEl.textContent = `인증됨: ${data.user || 'Unknown'}`;
      authStatusEl.style.color = '#22c55e';
      loginBtnEl.style.display = 'none';
      
      // 인증 성공 시 구독 목록 로드
      await loadSubscriptions();
    } else {
      authStatusEl.textContent = 'Azure 터미널 로그인 필요';
      authStatusEl.style.color = '#ef4444';
      loginBtnEl.style.display = 'inline-block';
      
      // 구독 드롭다운 비활성화
      subsEl.innerHTML = '<option value="">터미널에서 az login 필요</option>';
      subsEl.disabled = true;
      rgsEl.innerHTML = '<option value="">터미널에서 az login 필요</option>';
      rgsEl.disabled = true;
    }
  } catch (e) {
    authStatusEl.textContent = '인증 상태 확인 실패';
    authStatusEl.style.color = '#ef4444';
    loginBtnEl.style.display = 'inline-block';
    console.error('Auth status check failed:', e);
  }
}

// Azure 로그인 실행 (수동 안내만)
async function performLogin() {
  try {
    const r = await fetch('/api/auth/login', { method: 'POST' });
    const data = await r.json();
    
    if (data.manual_required) {
      addMessage('system', data.instruction);
      
      let stepsText = data.steps.join('\n');
      addMessage('assistant', `${stepsText}\n\n명령어:\n${data.manual_command}`);
      
      // 클립보드에 복사
      if (navigator.clipboard) {
        navigator.clipboard.writeText(data.manual_command).then(() => {
          addMessage('system', '명령어가 클립보드에 복사되었습니다.');
        }).catch(() => {
          addMessage('system', '명령어를 수동으로 복사해서 사용하세요.');
        });
      }
    }
  } catch (e) {
    addMessage('system', `오류: ${e.message}`);
  }
}

// 세션 초기화 버튼 이벤트
document.getElementById('reset-session')?.addEventListener('click', async () => {
  if (confirm('대화 기록과 세션을 초기화하시겠습니까?')) {
    // 새로운 세션 ID 생성
    sid = 'e' + Date.now().toString(36) + Math.random().toString(36).substr(2);
    
    // 채팅 기록 초기화
    document.getElementById('chat').innerHTML = '';
    
    // 드롭다운 초기화
    const subsEl = document.getElementById('subs');
    const rgsEl = document.getElementById('rgs');
    subsEl.selectedIndex = 0;
    rgsEl.innerHTML = '<option value="">구독을 먼저 선택하세요</option>';
    
    // 세션 ID 업데이트 (sidEl 사용)
    sidEl.textContent = sid;
    
    // 임시 알림 표시 (3초 후 사라짐)
    showTemporaryNotification('✅ 세션이 초기화되었습니다');
    
    // 서버에 새 세션 초기화 신호 전송 (빈 메시지)
    try {
      await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid, message: '__INIT_SESSION__' }),
      });
    } catch (e) {
      console.log('세션 초기화 신호 전송 실패:', e);
    }
  }
});

// 로그인 버튼 이벤트 (터미널 로그인 안내)
loginBtnEl?.addEventListener('click', performLogin);

// init
checkAuthStatus();

// 초기 인사말 추가
addMessage('bot', '안녕하세요! Azure 리소스 관리 도우미입니다. 🚀\n\n다음과 같은 명령어를 사용할 수 있습니다:\n• "내 구독 목록 보여줘" - 사용 가능한 구독 확인\n• "리소스 그룹 만들어줘" - 리소스 그룹 생성\n• "내 권한 확인해줘" - 현재 권한 확인\n\n무엇을 도와드릴까요?');
