const LANGS = {
  ru: { label: 'Русский', recognition: 'ru-RU', translation: 'ru' },
  he: { label: 'עברית', recognition: 'he-IL', translation: 'he' },
};
const TARGET_LANG = 'es-ES';
const TRANSLATE_URL = 'https://api.mymemory.translated.net/get';

const SpeechRecognitionAPI = window.SpeechRecognition || window.webkitSpeechRecognition;

const state = {
  source: 'ru',
  recognizer: null,
  filter: 'Все',
};

const el = {
  mic: document.getElementById('mic'),
  micLabel: document.getElementById('mic-label'),
  sourceText: document.getElementById('source-text'),
  translateBtn: document.getElementById('translate-btn'),
  result: document.getElementById('result'),
  resultSource: document.getElementById('result-source'),
  resultEs: document.getElementById('result-es'),
  playBtn: document.getElementById('play-btn'),
  status: document.getElementById('status'),
  filters: document.getElementById('filters'),
  phraseList: document.getElementById('phrase-list'),
};

function setStatus(message) {
  el.status.textContent = message;
}

// Нормализация для поиска фразы в разговорнике: регистр, знаки, лишние пробелы.
function normalize(text) {
  return text
    .toLowerCase()
    .replace(/[.,!?¿¡;:"'«»—…-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function findPhrase(text) {
  const query = normalize(text);
  if (!query) return null;
  return PHRASES.find((phrase) => normalize(phrase[state.source]) === query) || null;
}

function showResult(sourceText, spanishText) {
  el.resultSource.textContent = sourceText;
  el.resultEs.textContent = spanishText;
  el.result.hidden = false;
  setStatus('');
}

function speak(text) {
  if (!('speechSynthesis' in window)) {
    setStatus('Озвучка не поддерживается этим браузером.');
    return;
  }
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = TARGET_LANG;
  const voice = speechSynthesis.getVoices().find((v) => v.lang.startsWith('es'));
  if (voice) utterance.voice = voice;
  utterance.rate = 0.9;
  speechSynthesis.speak(utterance);
}

async function translate() {
  const text = el.sourceText.value.trim();
  if (!text) {
    setStatus('Сначала скажите или напишите фразу.');
    return;
  }

  const phrase = findPhrase(text);
  if (phrase) {
    el.sourceText.value = phrase[state.source];
    showResult(phrase[state.source], phrase.es);
    return;
  }

  setStatus('Перевожу…');
  el.translateBtn.disabled = true;
  try {
    const params = new URLSearchParams({
      q: text,
      langpair: `${LANGS[state.source].translation}|es`,
    });
    const response = await fetch(`${TRANSLATE_URL}?${params}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (data.responseStatus && data.responseStatus !== 200) {
      throw new Error(data.responseDetails || `статус ${data.responseStatus}`);
    }
    const translated = data.responseData && data.responseData.translatedText;
    if (!translated) throw new Error('пустой ответ');
    showResult(text, translated);
  } catch (error) {
    console.error(error);
    el.result.hidden = true;
    setStatus(`Не удалось перевести (${error.message}). Проверьте интернет или попробуйте фразу из разговорника.`);
  } finally {
    el.translateBtn.disabled = false;
  }
}

function setListening(isListening) {
  el.mic.classList.toggle('is-listening', isListening);
  el.micLabel.textContent = isListening ? 'Слушаю… нажмите, чтобы остановить' : 'Нажмите и говорите';
}

function startListening() {
  if (!SpeechRecognitionAPI) {
    setStatus('Распознавание речи не поддерживается этим браузером. Введите текст вручную.');
    return;
  }

  const recognizer = new SpeechRecognitionAPI();
  recognizer.lang = LANGS[state.source].recognition;
  recognizer.interimResults = true;
  recognizer.continuous = false;
  recognizer.maxAlternatives = 1;

  let finalText = '';

  recognizer.onresult = (event) => {
    let interim = '';
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      if (result.isFinal) {
        finalText += result[0].transcript;
      } else {
        interim += result[0].transcript;
      }
    }
    el.sourceText.value = (finalText + interim).trim();
  };

  recognizer.onerror = (event) => {
    const messages = {
      'not-allowed': 'Нет доступа к микрофону. Разрешите его в настройках браузера.',
      'no-speech': 'Речь не распознана. Попробуйте ещё раз.',
      'audio-capture': 'Микрофон не найден.',
      network: 'Ошибка сети при распознавании речи.',
    };
    setStatus(messages[event.error] || `Ошибка распознавания: ${event.error}`);
  };

  recognizer.onend = () => {
    state.recognizer = null;
    setListening(false);
    if (el.sourceText.value.trim()) translate();
  };

  state.recognizer = recognizer;
  setStatus('');
  setListening(true);
  recognizer.start();
}

function toggleMic() {
  if (state.recognizer) {
    state.recognizer.stop();
  } else {
    startListening();
  }
}

function setSource(lang) {
  state.source = lang;
  document.querySelectorAll('.lang-btn').forEach((btn) => {
    const active = btn.dataset.lang === lang;
    btn.classList.toggle('is-active', active);
    btn.setAttribute('aria-checked', String(active));
  });
  el.sourceText.value = '';
  el.sourceText.lang = lang;
  el.sourceText.dir = lang === 'he' ? 'rtl' : 'ltr';
  el.result.hidden = true;
  setStatus('');
  renderPhrases();
}

function renderFilters() {
  const categories = ['Все', ...new Set(PHRASES.map((p) => p.category))];
  el.filters.innerHTML = '';
  categories.forEach((category) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chip' + (category === state.filter ? ' is-active' : '');
    btn.textContent = category;
    btn.addEventListener('click', () => {
      state.filter = category;
      renderFilters();
      renderPhrases();
    });
    el.filters.appendChild(btn);
  });
}

function renderPhrases() {
  const visible = PHRASES.filter((p) => state.filter === 'Все' || p.category === state.filter);
  el.phraseList.innerHTML = '';

  visible.forEach((phrase) => {
    const item = document.createElement('li');
    item.className = 'phrase';

    const pick = document.createElement('button');
    pick.type = 'button';
    pick.className = 'phrase-pick';
    pick.setAttribute('aria-label', `Показать перевод: ${phrase[state.source]}`);

    const source = document.createElement('span');
    source.className = 'phrase-source';
    source.lang = state.source;
    source.dir = 'auto';
    source.textContent = phrase[state.source];

    const es = document.createElement('span');
    es.className = 'phrase-es';
    es.lang = 'es';
    es.textContent = phrase.es;

    pick.append(source, es);
    pick.addEventListener('click', () => {
      el.sourceText.value = phrase[state.source];
      showResult(phrase[state.source], phrase.es);
    });

    const play = document.createElement('button');
    play.type = 'button';
    play.className = 'icon-btn';
    play.setAttribute('aria-label', `Воспроизвести: ${phrase.es}`);
    play.textContent = '▶';
    play.addEventListener('click', () => speak(phrase.es));

    item.append(pick, play);
    el.phraseList.appendChild(item);
  });
}

document.querySelectorAll('.lang-btn').forEach((btn) => {
  btn.addEventListener('click', () => setSource(btn.dataset.lang));
});

el.mic.addEventListener('click', toggleMic);
el.translateBtn.addEventListener('click', translate);
el.playBtn.addEventListener('click', () => speak(el.resultEs.textContent));
el.sourceText.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    translate();
  }
});

if (!SpeechRecognitionAPI) {
  el.mic.disabled = true;
  el.micLabel.textContent = 'Микрофон недоступен в этом браузере';
}

el.sourceText.lang = state.source;
el.sourceText.dir = 'ltr';
renderFilters();
renderPhrases();
