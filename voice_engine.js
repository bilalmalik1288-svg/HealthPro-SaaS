document.addEventListener("DOMContentLoaded", function() {
    
    // ==========================================================
    // 1. GLOBAL SEARCH IMPLEMENTATION
    // ==========================================================
    const globalSearch = document.getElementById('global-search');
    if (globalSearch) {
        globalSearch.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                const query = this.value.trim();
                if (query) {
                    window.location.href = '/search?q=' + encodeURIComponent(query);
                }
            }
        });
    }

    // ==========================================================
    // 2. AI CHATBOT IMPLEMENTATION
    // ==========================================================
    const chatBtn = document.getElementById('ai-chat-btn');
    const chatWindow = document.getElementById('ai-chat-window');
    const chatClose = document.getElementById('ai-chat-close');
    const chatSend = document.getElementById('ai-chat-send');
    const chatInput = document.getElementById('ai-chat-input');
    const chatBody = document.getElementById('ai-chat-body');

    if (chatBtn) {
        chatBtn.addEventListener('click', () => { 
            chatWindow.classList.remove('hidden'); 
            chatBtn.classList.add('hidden'); 
        });
        chatClose.addEventListener('click', () => { 
            chatWindow.classList.add('hidden'); 
            chatBtn.classList.remove('hidden'); 
        });
    }

    function appendMessage(text, sender) {
        const msgDiv = document.createElement('div');
        msgDiv.classList.add('ai-message', sender);
        msgDiv.innerText = text;
        if (chatBody) { 
            chatBody.appendChild(msgDiv); 
            chatBody.scrollTop = chatBody.scrollHeight; 
        }
    }

    if (chatSend) {
        chatSend.addEventListener('click', sendMessage);
        chatInput.addEventListener('keypress', function(e) { 
            if (e.key === 'Enter' && e.target.id === 'ai-chat-input') {
                sendMessage();
            } 
        });
    }

    function sendMessage() {
        const text = chatInput.value.trim();
        if (!text) {
            return;
        }
        appendMessage(text, 'user');
        chatInput.value = '';

        const typingDiv = document.createElement('div');
        typingDiv.classList.add('ai-message', 'bot', 'typing');
        typingDiv.innerText = "Typing...";
        if (chatBody) { 
            chatBody.appendChild(typingDiv); 
            chatBody.scrollTop = chatBody.scrollHeight; 
        }

        fetch('/ai/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text })
        })
        .then(response => response.json())
        .then(data => {
            if (chatBody && chatBody.contains(typingDiv)) {
                chatBody.removeChild(typingDiv);
            }
            if (data.success) { 
                appendMessage(data.reply, 'bot'); 
            } else { 
                appendMessage("Error: " + data.reply, 'bot'); 
            }
        })
        .catch(err => {
            if (chatBody && chatBody.contains(typingDiv)) {
                chatBody.removeChild(typingDiv);
            }
            appendMessage("Connection error. Please try again.", 'bot');
        });
    }

    // ==========================================================
    // 3. USA ENTERPRISE EHR: VOICE AI SYSTEM-WIDE ENGINE
    // ==========================================================
    const voiceBtn = document.getElementById('voice-cmd-btn');
    const voiceStatus = document.getElementById('voice-status');

    if (window.SpeechRecognition || window.webkitSpeechRecognition) {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        const recognition = new SpeechRecognition();
        
        recognition.continuous = false;
        recognition.interimResults = false;
        recognition.lang = 'en-US';

        if (voiceBtn) {
            voiceBtn.addEventListener('click', () => {
                recognition.start();
                voiceStatus.classList.remove('voice-status-hidden');
                voiceStatus.classList.add('voice-status-active');
                voiceStatus.innerText = "Listening... (Speak command or dictate)";
                voiceBtn.classList.add('recording-pulse');
            });
        }

        recognition.onresult = function(event) {
            const transcript = event.results[0][0].transcript.trim();
            const lowerTranscript = transcript.toLowerCase();
            
            if (voiceStatus) {
                voiceStatus.innerText = `Heard: "${transcript}"`;
            }
            
            setTimeout(() => {
                if (voiceStatus) {
                    voiceStatus.classList.remove('voice-status-active');
                    voiceStatus.classList.add('voice-status-hidden');
                }
                if (voiceBtn) {
                    voiceBtn.classList.remove('recording-pulse');
                }
            }, 1500);

            const activeElement = document.activeElement;

            // 1. DICTATION & CHAT MODE (If user is focused on an input box)
            if (activeElement && (activeElement.tagName === 'INPUT' || activeElement.tagName === 'TEXTAREA')) {
                
                // Special AI Chat auto-send trigger
                if (activeElement.id === 'ai-chat-input' && lowerTranscript.includes('send message')) {
                    const cleanText = transcript.replace(/send message/i, '').trim();
                    if (cleanText) {
                        activeElement.value += (activeElement.value ? ' ' : '') + cleanText;
                    }
                    if (chatSend) {
                        chatSend.click(); 
                    }
                } else {
                    // Normal Dictation (Appends text automatically)
                    activeElement.value += (activeElement.value ? ' ' : '') + transcript;
                }
            } 
            // 2. SYSTEM-WIDE NAVIGATION & ACTION COMMANDS
            else {
                if (lowerTranscript.includes('dashboard')) {
                    window.location.href = '/';
                }
                else if (lowerTranscript.includes('patients') && !lowerTranscript.includes('add')) {
                    window.location.href = '/patients';
                }
                else if (lowerTranscript.includes('add patient')) {
                    window.location.href = '/patients/add';
                }
                else if (lowerTranscript.includes('appointments') || lowerTranscript.includes('schedule')) {
                    window.location.href = '/appointments';
                }
                else if (lowerTranscript.includes('billing') || lowerTranscript.includes('invoices')) {
                    window.location.href = '/billing';
                }
                else if (lowerTranscript.includes('messages') || lowerTranscript.includes('inbox')) {
                    window.location.href = '/messages/inbox';
                }
                // Form Action Commands
                else if (lowerTranscript.includes('save') || lowerTranscript.includes('submit')) {
                    const submitBtn = document.querySelector('button[type="submit"]');
                    if (submitBtn) {
                        if (voiceStatus) {
                            voiceStatus.innerText = "Saving form...";
                        }
                        submitBtn.click();
                    } else {
                        if (voiceStatus) {
                            voiceStatus.innerText = "No form detected to save.";
                        }
                    }
                }
                // AI Chat Widget Commands
                else if (lowerTranscript.includes('open chat') || lowerTranscript.includes('assistant')) {
                    if (chatBtn) {
                        chatBtn.click();
                    }
                }
                else if (lowerTranscript.includes('close chat')) {
                    if (chatClose) {
                        chatClose.click();
                    }
                }
                // Global Search Command
                else if (lowerTranscript.includes('search')) {
                    let query = lowerTranscript.replace('search', '').trim();
                    if (query) {
                        window.location.href = '/search?q=' + encodeURIComponent(query);
                    }
                }
            }
        };

        recognition.onerror = function(event) {
            if (voiceStatus) {
                voiceStatus.innerText = "Error: Voice not recognized.";
            }
            setTimeout(() => {
                if (voiceStatus) {
                    voiceStatus.classList.remove('voice-status-active');
                    voiceStatus.classList.add('voice-status-hidden');
                }
                if (voiceBtn) {
                    voiceBtn.classList.remove('recording-pulse');
                }
            }, 2000);
        };
    } else {
        if (voiceBtn) {
            voiceBtn.style.display = 'none'; // Browser fallback
        }
    }
});