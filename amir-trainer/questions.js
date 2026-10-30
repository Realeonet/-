// Банк вопросов для тренажёра Амир (английский).
// Типы разделов: grammar (грамматика), vocab (лексика),
// restatement (выбор фразы с тем же смыслом), reading (понимание текста).
// Поле answer — индекс правильного варианта (0..3).
// Формат приблизительный: актуальную структуру уточняйте на сайте NITE.

window.QUESTIONS = [
  // ---------- GRAMMAR ----------
  {
    id: "g1", section: "grammar",
    q: "She ___ in London since 2019.",
    options: ["lives", "has lived", "lived", "is living"],
    answer: 1,
    explain: "С «since + момент в прошлом» используем Present Perfect: has lived."
  },
  {
    id: "g2", section: "grammar",
    q: "If I ___ more time, I would travel more.",
    options: ["have", "had", "will have", "having"],
    answer: 1,
    explain: "Условие 2-го типа (нереальное в настоящем): if + Past Simple, would + глагол."
  },
  {
    id: "g3", section: "grammar",
    q: "The report, along with the charts, ___ on the desk.",
    options: ["are", "were", "is", "have been"],
    answer: 2,
    explain: "Подлежащее — «the report» (ед. число). «Along with the charts» не меняет согласование."
  },
  {
    id: "g4", section: "grammar",
    q: "Neither the teachers nor the principal ___ aware of the problem.",
    options: ["was", "were", "are", "be"],
    answer: 0,
    explain: "При neither…nor глагол согласуется с ближайшим подлежащим: the principal → was."
  },
  {
    id: "g5", section: "grammar",
    q: "I wish I ___ the answer to that question yesterday.",
    options: ["knew", "had known", "know", "would know"],
    answer: 1,
    explain: "Сожаление о прошлом: wish + Past Perfect → had known."
  },
  {
    id: "g6", section: "grammar",
    q: "By the time we arrived, the film ___.",
    options: ["had already started", "already started", "has already started", "was already starting"],
    answer: 0,
    explain: "Действие, завершившееся раньше другого прошлого действия: Past Perfect."
  },
  {
    id: "g7", section: "grammar",
    q: "He suggested that she ___ a doctor.",
    options: ["sees", "see", "to see", "will see"],
    answer: 1,
    explain: "После suggest/recommend that + базовая форма глагола (без -s): see."
  },
  {
    id: "g8", section: "grammar",
    q: "Hardly ___ the door when the phone rang.",
    options: ["I had closed", "had I closed", "I closed", "did I close"],
    answer: 1,
    explain: "После отрицательного наречия hardly в начале предложения — инверсия: had I closed."
  },
  {
    id: "g9", section: "grammar",
    q: "The museum, ___ was built in 1900, is closed on Mondays.",
    options: ["that", "which", "what", "where"],
    answer: 1,
    explain: "В уточняющем придаточном (с запятыми) «that» не используют — только which/who."
  },
  {
    id: "g10", section: "grammar",
    q: "Despite ___ tired, she finished the marathon.",
    options: ["being", "to be", "been", "be"],
    answer: 0,
    explain: "После despite/in spite of — герундий: being tired."
  },
  {
    id: "g11", section: "grammar",
    q: "He is used to ___ early.",
    options: ["wake up", "waking up", "wake", "woke"],
    answer: 1,
    explain: "be used to + -ing (привык к чему-то): waking up."
  },
  {
    id: "g12", section: "grammar",
    q: "You ___ smoke here; it is strictly forbidden.",
    options: ["mustn't", "needn't", "don't have to", "might not"],
    answer: 0,
    explain: "mustn't = нельзя (запрет). needn't/don't have to = не нужно (отсутствие необходимости)."
  },
  {
    id: "g13", section: "grammar",
    q: "The meeting has been postponed ___ next week.",
    options: ["until", "to", "at", "on"],
    answer: 0,
    explain: "postpone until/till + время: until next week."
  },

  // ---------- VOCABULARY ----------
  {
    id: "v1", section: "vocab",
    q: "The company decided to ___ its workforce by 20% because of financial problems.",
    options: ["reduce", "increase", "improve", "expand"],
    answer: 0,
    explain: "Сократить штат — reduce (workforce). Остальные варианты означают «увеличить/улучшить/расширить»."
  },
  {
    id: "v2", section: "vocab",
    q: "Her explanation was so ___ that everyone understood it at once.",
    options: ["vague", "clear", "confusing", "complicated"],
    answer: 1,
    explain: "Понятное объяснение — clear. Vague = расплывчатое, confusing/complicated = запутанное."
  },
  {
    id: "v3", section: "vocab",
    q: "The new law will ___ effect next month.",
    options: ["take", "come", "go", "put"],
    answer: 0,
    explain: "Устойчивое сочетание: take effect (вступать в силу)."
  },
  {
    id: "v4", section: "vocab",
    q: "He is very ___; he never changes his mind, even when he is wrong.",
    options: ["flexible", "stubborn", "generous", "cheerful"],
    answer: 1,
    explain: "Упрямый, не меняющий мнения — stubborn."
  },
  {
    id: "v5", section: "vocab",
    q: "The scientist's ___ discovery changed medicine forever.",
    options: ["groundbreaking", "ordinary", "minor", "temporary"],
    answer: 0,
    explain: "Groundbreaking = революционный, новаторский. Подходит по смыслу «изменил медицину навсегда»."
  },
  {
    id: "v6", section: "vocab",
    q: "The price of petrol has ___ dramatically over the last year.",
    options: ["risen", "raised", "arisen", "arose"],
    answer: 0,
    explain: "Rise (непереходный) — rose — risen. Raise (переходный) требует дополнения: raise the price."
  },
  {
    id: "v7", section: "vocab",
    q: "I'm afraid I can't ___ this noise any longer.",
    options: ["tolerate", "tolerant", "tolerance", "tolerable"],
    answer: 0,
    explain: "После can't нужен глагол: tolerate (терпеть)."
  },
  {
    id: "v8", section: "vocab",
    q: "The witness ___ the man at the scene of the crime yesterday.",
    options: ["identified", "identifying", "identity", "identify"],
    answer: 0,
    explain: "Маркер «yesterday» → Past Simple: identified."
  },

  // ---------- RESTATEMENT ----------
  {
    id: "r1", section: "restatement",
    q: "Choose the sentence that has the SAME meaning as: \"Although he was tired, he kept working.\"",
    options: [
      "He kept working, although he was tired.",
      "He was tired, so he stopped working.",
      "Being tired, he didn't work.",
      "He was tired because he kept working."
    ],
    answer: 0,
    explain: "Although = несмотря на то что. Переставить части предложения можно без изменения смысла."
  },
  {
    id: "r2", section: "restatement",
    q: "Choose the sentence that has the SAME meaning as: \"It's a pity I didn't study harder.\"",
    options: [
      "I wish I had studied harder.",
      "I wish I studied harder.",
      "I hope I study harder.",
      "I regret studying harder."
    ],
    answer: 0,
    explain: "Сожаление о прошлом: I wish I had + V3."
  },
  {
    id: "r3", section: "restatement",
    q: "Choose the sentence that has the SAME meaning as: \"She is too young to vote.\"",
    options: [
      "She is young enough to vote.",
      "She isn't old enough to vote.",
      "She is not young enough to vote.",
      "She is old enough to vote."
    ],
    answer: 1,
    explain: "too young to = not old enough to."
  },
  {
    id: "r4", section: "restatement",
    q: "Choose the sentence that has the SAME meaning as: \"The last time I saw him was in 2015.\"",
    options: [
      "I haven't seen him since 2015.",
      "I didn't see him since 2015.",
      "I have seen him since 2015.",
      "I have never seen him in 2015."
    ],
    answer: 0,
    explain: "The last time I saw… = I haven't seen… since…"
  },
  {
    id: "r5", section: "restatement",
    q: "Choose the sentence that has the SAME meaning as: \"Nobody told me about the change.\"",
    options: [
      "Someone told me about the change.",
      "I wasn't told about the change.",
      "I told everyone about the change.",
      "The change was told to me."
    ],
    answer: 1,
    explain: "Nobody told me = I wasn't told (пассивный залог с сохранением смысла)."
  },

  // ---------- READING ----------
  {
    id: "rd1", section: "reading",
    passage: "Many people believe that learning a second language is only useful for travel. However, research suggests that bilingual people often have better memory and are better at switching between tasks. Scientists think this is because their brains practise controlling attention every time they choose between two languages. Furthermore, learning a language can improve understanding of one's own mother tongue, since students begin to notice grammar patterns they had never thought about before.",
    q: "According to the passage, what do many people wrongly believe?",
    options: [
      "Languages are very hard to learn.",
      "Second languages are useful only for travel.",
      "Bilingual people have a poor memory.",
      "Grammar is unimportant."
    ],
    answer: 1,
    explain: "В тексте прямо сказано: многие считают, что второй язык нужен только для путешествий."
  },
  {
    id: "rd2", section: "reading",
    passage: "Many people believe that learning a second language is only useful for travel. However, research suggests that bilingual people often have better memory and are better at switching between tasks. Scientists think this is because their brains practise controlling attention every time they choose between two languages. Furthermore, learning a language can improve understanding of one's own mother tongue, since students begin to notice grammar patterns they had never thought about before.",
    q: "Why do scientists think bilingual people have better attention control?",
    options: [
      "They study for more hours.",
      "Their brains practise choosing between two languages.",
      "They travel more often.",
      "They forget their mother tongue."
    ],
    answer: 1,
    explain: "Ответ — в предложении про «brains practise controlling attention every time they choose between two languages»."
  },
  {
    id: "rd3", section: "reading",
    passage: "Many people believe that learning a second language is only useful for travel. However, research suggests that bilingual people often have better memory and are better at switching between tasks. Scientists think this is because their brains practise controlling attention every time they choose between two languages. Furthermore, learning a language can improve understanding of one's own mother tongue, since students begin to notice grammar patterns they had never thought about before.",
    q: "What does the passage say about learning a language and one's mother tongue?",
    options: [
      "It can make the mother tongue harder to understand.",
      "It can improve understanding of the mother tongue.",
      "It has no effect on the mother tongue.",
      "It replaces the mother tongue."
    ],
    answer: 1,
    explain: "Слово «Furthermore» добавляет ещё один плюс: улучшается понимание родного языка."
  },
  {
    id: "rd4", section: "reading",
    passage: "The city council has announced plans to build a new cycle lane along the river. The project, which will cost about two million dollars, is expected to take eighteen months. Local shop owners have expressed concern that fewer customers will be able to park nearby. However, the council argues that the lane will reduce traffic and make the area safer for children.",
    q: "What is the main purpose of the passage?",
    options: [
      "To criticise the council's budget.",
      "To report a planned project and the reactions to it.",
      "To advertise a new shop.",
      "To explain how to park a car."
    ],
    answer: 1,
    explain: "Текст описывает проект, его стоимость и сроки, а также мнения владельцев магазинов и совета."
  },
  {
    id: "rd5", section: "reading",
    passage: "The city council has announced plans to build a new cycle lane along the river. The project, which will cost about two million dollars, is expected to take eighteen months. Local shop owners have expressed concern that fewer customers will be able to park nearby. However, the council argues that the lane will reduce traffic and make the area safer for children.",
    q: "What concern do the shop owners have?",
    options: [
      "The project is too cheap.",
      "Customers may find it harder to park.",
      "The river is dangerous.",
      "The lane is too short."
    ],
    answer: 1,
    explain: "Владельцы беспокоятся, что клиентам станет труднее парковаться рядом."
  },
  {
    id: "rd6", section: "reading",
    passage: "The city council has announced plans to build a new cycle lane along the river. The project, which will cost about two million dollars, is expected to take eighteen months. Local shop owners have expressed concern that fewer customers will be able to park nearby. However, the council argues that the lane will reduce traffic and make the area safer for children.",
    q: "According to the council, what is one benefit of the lane?",
    options: [
      "It will increase shop prices.",
      "It will reduce traffic and improve safety for children.",
      "It will close the river.",
      "It will last only one month."
    ],
    answer: 1,
    explain: "Совет утверждает, что полоса уменьшит пробки и сделает район безопаснее для детей."
  }
];
