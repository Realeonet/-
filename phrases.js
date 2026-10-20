// Разговорник: фразы с готовым испанским переводом.
// Поля: category — раздел, ru / he — исходный текст, es — перевод.
const PHRASES = [
  { category: 'Приветствия', ru: 'Привет', he: 'שלום', es: 'Hola' },
  { category: 'Приветствия', ru: 'Доброе утро', he: 'בוקר טוב', es: 'Buenos días' },
  { category: 'Приветствия', ru: 'Добрый вечер', he: 'ערב טוב', es: 'Buenas tardes' },
  { category: 'Приветствия', ru: 'Спокойной ночи', he: 'לילה טוב', es: 'Buenas noches' },
  { category: 'Приветствия', ru: 'До свидания', he: 'להתראות', es: 'Adiós' },
  { category: 'Приветствия', ru: 'Хорошего дня', he: 'יום טוב', es: 'Que tenga un buen día' },

  { category: 'Базовое', ru: 'Да', he: 'כן', es: 'Sí' },
  { category: 'Базовое', ru: 'Нет', he: 'לא', es: 'No' },
  { category: 'Базовое', ru: 'Спасибо', he: 'תודה', es: 'Gracias' },
  { category: 'Базовое', ru: 'Пожалуйста', he: 'בבקשה', es: 'Por favor' },
  { category: 'Базовое', ru: 'Извините', he: 'סליחה', es: 'Perdón' },
  { category: 'Базовое', ru: 'Я не понимаю', he: 'אני לא מבין', es: 'No entiendo' },
  { category: 'Базовое', ru: 'Говорите медленнее', he: 'תדבר לאט', es: 'Hable más despacio' },
  { category: 'Базовое', ru: 'Я говорю немного по-испански', he: 'אני מדבר קצת ספרדית', es: 'Hablo un poco de español' },
  { category: 'Базовое', ru: 'Как вас зовут?', he: 'איך קוראים לך?', es: '¿Cómo se llama?' },
  { category: 'Базовое', ru: 'Меня зовут…', he: 'קוראים לי…', es: 'Me llamo…' },
  { category: 'Базовое', ru: 'Сколько времени?', he: 'מה השעה?', es: '¿Qué hora es?' },

  { category: 'Ресторан', ru: 'Меню, пожалуйста', he: 'תפריט, בבקשה', es: 'La carta, por favor' },
  { category: 'Ресторан', ru: 'Вода, пожалуйста', he: 'מים, בבקשה', es: 'Agua, por favor' },
  { category: 'Ресторан', ru: 'Счёт, пожалуйста', he: 'החשבון, בבקשה', es: 'La cuenta, por favor' },
  { category: 'Ресторан', ru: 'Сколько это стоит?', he: 'כמה זה עולה?', es: '¿Cuánto cuesta?' },
  { category: 'Ресторан', ru: 'Очень вкусно!', he: 'טעים מאוד!', es: '¡Qué rico!' },
  { category: 'Ресторан', ru: 'Где туалет?', he: 'איפה השירותים?', es: '¿Dónde está el baño?' },

  { category: 'Транспорт', ru: 'Один билет, пожалуйста', he: 'כרטיס אחד, בבקשה', es: 'Un billete, por favor' },
  { category: 'Транспорт', ru: 'Где остановка автобуса?', he: 'איפה התחנה של האוטובוס?', es: '¿Dónde está la parada de autobús?' },
  { category: 'Транспорт', ru: 'Где здесь метро?', he: 'איפה הרכבת התחתית?', es: '¿Dónde está el metro?' },
  { category: 'Транспорт', ru: 'Отвезите меня сюда, пожалуйста', he: 'תיקח אותי לכאן, בבקשה', es: 'Llévame aquí, por favor' },

  { category: 'Покупки', ru: 'Можно посмотреть?', he: 'אפשר לראות?', es: '¿Puedo verlo?' },
  { category: 'Покупки', ru: 'Это слишком дорого', he: 'זה יקר מדי', es: 'Es demasiado caro' },
  { category: 'Покупки', ru: 'Можно оплатить картой?', he: 'אפשר לשלם בכרטיס?', es: '¿Puedo pagar con tarjeta?' },

  { category: 'Помощь', ru: 'Помогите!', he: 'עזרה!', es: '¡Socorro!' },
  { category: 'Помощь', ru: 'Мне нужна помощь', he: 'אני צריך עזרה', es: 'Necesito ayuda' },
  { category: 'Помощь', ru: 'Вызовите врача', he: 'תקראו לרופא', es: 'Llame a un médico' },
  { category: 'Помощь', ru: 'Где ближайшая аптека?', he: 'איפה בית המרקחת הכי קרוב?', es: '¿Dónde está la farmacia más cercana?' },
  { category: 'Помощь', ru: 'Я потерялся', he: 'אני אבוד', es: 'Me he perdido' },
];
