/**
 * 空き家アトラス お問い合わせフォーム（Google Apps Script）。
 *
 * setup() を一度実行すると、次をまとめて行う:
 *   1. Google フォームの作成（質問: お名前 / メールアドレス（返信先）/ 種別（選択）/ 内容）。
 *      回答者に Google ログインは求めない（メールアドレスの自動収集は使わず、返信は入力欄のアドレスにだけ送る）
 *   2. 回答先スプレッドシートの作成とフォームへのリンク、「自動返信ログ」シートの用意
 *   3. フォーム送信時トリガー（onFormSubmit）の登録
 *   4. GitHub のラベル（takedown / municipality / needs-human）の用意（GITHUB_TOKEN が設定済みのとき）
 *   5. 完了時にフォーム URL（回答用・編集用）と回答スプレッドシートの URL をログに出力
 *
 * 送信時の処理（AI による分類・自動返信・Issue 作成・ログ記録）は AutoReply.gs にある。
 * API キーと GitHub トークンはスクリプトプロパティにだけ置き、コードには書かない（README の表）。
 *
 * 再実行しても二重には作らない。作成済みの ID をスクリプトプロパティに保存し、無いものだけ作る。
 * 種別や文言を変えるときは CONFIG を書き換えて setup() を再実行する（既存フォームの質問は変えないので、
 * 質問を作り直したいときは reset() で ID を忘れさせてから setup() を実行する。フォーム自体は削除しない）。
 */

const CONFIG = {
  formTitle: '空き家アトラス お問い合わせ',
  formDescription:
    '掲載内容の訂正・削除のご依頼、自治体・移住推進組織からのご連絡、その他のお問い合わせはこちらから。' +
    '物件の紹介・仲介は行っていません。送信後に自動返信メールが届きます（内容の分類と返信文の作成に AI を使います）。' +
    '回答には数日いただく場合があります。',
  // 種別（選択肢）。順番どおりに表示される
  categories: [
    '掲載内容の訂正・削除の依頼',
    '自治体・移住推進組織からのご連絡',
    '空き家の所有者からのご相談',
    '取材・提携のご相談',
    'その他',
  ],
  sheetName: '空き家アトラス お問い合わせ（回答）',
  // 分類結果と返信内容を記録するシート（回答スプレッドシート内に作る）
  logSheetName: '自動返信ログ',
  // 運営者への通知先。スクリプトプロパティ NOTIFY_TO があればそちらを優先。どちらも空なら実行者宛て
  notifyTo: '',
  confirmationMessage:
    'お問い合わせを受け付けました。まもなく自動返信メールが届きます。届かない場合は迷惑メールをご確認ください。',
  // サイトの基準 URL（返信文のリンクに使う）。スクリプトプロパティ SITE_BASE_URL で上書きできる
  siteBaseUrl: 'https://akiya-atlas.com',
  // 返信の署名に使う運営者名。スクリプトプロパティ OPERATOR_NAME で上書きできる（site.toml の [operator] name と合わせる）
  operatorName: '空き家アトラス 運営',
  // 分類と返信文生成に使うモデル。スクリプトプロパティ ANTHROPIC_MODEL で上書きできる
  model: 'claude-haiku-4-5',
  // 同一メールアドレスへの自動返信はこの時間内に 1 通まで
  replyLimitHours: 24,
  // 運営者への通知メール（スパム判定のものは送らない）
  notifyOperator: true,
  // GitHub Issue のラベル（setup() が無ければ作る）
  githubLabels: {
    takedown: { name: 'takedown', color: 'B60205', description: '掲載内容の訂正・削除の依頼（お問い合わせフォームから自動起票）' },
    municipality: { name: 'municipality', color: '0E8A16', description: '自治体・移住推進組織からの連絡（お問い合わせフォームから自動起票）' },
    needsHuman: { name: 'needs-human', color: 'FBCA04', description: '人が判断する問い合わせ（お問い合わせフォームから自動起票）' },
  },
};

// スクリプトプロパティのキー。作成物の ID はコードが保存する。秘密情報と環境ごとの設定は人が入れる（README）
const PROP_FORM_ID = 'CONTACT_FORM_ID';
const PROP_SHEET_ID = 'CONTACT_SHEET_ID';
const PROP_ANTHROPIC_API_KEY = 'ANTHROPIC_API_KEY';
const PROP_GITHUB_TOKEN = 'GITHUB_TOKEN';
const PROP_GITHUB_REPO = 'GITHUB_REPO';
const PROP_NOTIFY_TO = 'NOTIFY_TO';
const PROP_SITE_BASE_URL = 'SITE_BASE_URL';
const PROP_OPERATOR_NAME = 'OPERATOR_NAME';
const PROP_MODEL = 'ANTHROPIC_MODEL';

const TRIGGER_HANDLER = 'onFormSubmit';
const Q_NAME = 'お名前';
const Q_EMAIL = 'メールアドレス（返信先）';
const Q_CATEGORY = '種別';
const Q_BODY = '内容';

const LOG_HEADER = [
  '受付日時',
  'メール',
  'お名前',
  '選択した種別',
  'AI 分類',
  '確信度',
  '対応',
  '返信件名',
  '返信本文',
  'Issue URL',
  'モデル',
  'エラー',
  '判定理由',
  '要約',
];

/** すべてを一度に用意する。完了するとフォームの回答用 URL を返し、ログにも出す。 */
function setup() {
  const props = PropertiesService.getScriptProperties();
  const form = getOrCreateForm_(props);
  const ss = getOrCreateSheet_(props, form);
  ensureLogSheet_(ss);
  ensureSubmitTrigger_(form);
  const labels = ensureGithubLabels_();
  Logger.log('フォーム（回答用 URL）: %s', form.getPublishedUrl());
  Logger.log('フォーム（編集用 URL）: %s', form.getEditUrl());
  Logger.log('回答スプレッドシート: %s', ss.getUrl());
  Logger.log('送信時トリガー: %s（登録済み）', TRIGGER_HANDLER);
  Logger.log('GitHub ラベル: %s', labels);
  const missing = missingProperties_();
  if (missing.length) {
    Logger.log(
      '注意: スクリプトプロパティが未設定: %s。設定するまで AI 分類と自動返信は動かず、運営者への通知だけ行う',
      missing.join(', ')
    );
  }
  return form.getPublishedUrl();
}

/** 保存した ID を忘れる（フォームやシートは削除しない）。次の setup() で新しく作る。 */
function reset() {
  const props = PropertiesService.getScriptProperties();
  props.deleteProperty(PROP_FORM_ID);
  props.deleteProperty(PROP_SHEET_ID);
  Logger.log('保存していた ID を消しました。次の setup() で新しいフォームとシートを作ります。');
}

/** スクリプトプロパティと CONFIG をまとめた実行時設定。 */
function settings_() {
  const p = PropertiesService.getScriptProperties();
  return {
    apiKey: p.getProperty(PROP_ANTHROPIC_API_KEY) || '',
    githubToken: p.getProperty(PROP_GITHUB_TOKEN) || '',
    githubRepo: p.getProperty(PROP_GITHUB_REPO) || '',
    notifyTo: p.getProperty(PROP_NOTIFY_TO) || CONFIG.notifyTo || Session.getEffectiveUser().getEmail(),
    siteBaseUrl: (p.getProperty(PROP_SITE_BASE_URL) || CONFIG.siteBaseUrl).replace(/\/+$/, ''),
    operatorName: p.getProperty(PROP_OPERATOR_NAME) || CONFIG.operatorName,
    model: p.getProperty(PROP_MODEL) || CONFIG.model,
    sheetId: p.getProperty(PROP_SHEET_ID) || '',
  };
}

/** 必須のスクリプトプロパティのうち未設定のもの。 */
function missingProperties_() {
  const p = PropertiesService.getScriptProperties();
  return [PROP_ANTHROPIC_API_KEY, PROP_GITHUB_TOKEN, PROP_GITHUB_REPO].filter((k) => !p.getProperty(k));
}

function getOrCreateForm_(props) {
  const id = props.getProperty(PROP_FORM_ID);
  if (id) {
    try {
      return FormApp.openById(id);
    } catch (e) {
      Logger.log('保存されていたフォーム（%s）を開けないので作り直します: %s', id, e);
    }
  }
  const form = FormApp.create(CONFIG.formTitle);
  form.setDescription(CONFIG.formDescription);
  // メールアドレスの自動収集（setCollectEmail）は使わない。回答者に Google ログインを求めず、返信先は入力欄の値だけを使う
  form.setLimitOneResponsePerUser(false);
  form.setAllowResponseEdits(false);
  form.setConfirmationMessage(CONFIG.confirmationMessage);

  form.addTextItem().setTitle(Q_NAME).setRequired(true);
  form
    .addTextItem()
    .setTitle(Q_EMAIL)
    .setHelpText('自動返信と回答をお送りします。')
    .setValidation(FormApp.createTextValidation().requireTextIsEmail().build())
    .setRequired(true);
  form.addMultipleChoiceItem().setTitle(Q_CATEGORY).setChoiceValues(CONFIG.categories).setRequired(true);
  form
    .addParagraphTextItem()
    .setTitle(Q_BODY)
    .setHelpText('対象のページ URL や物件番号があれば添えてください。個人情報や写真は書かないでください。')
    .setRequired(true);

  props.setProperty(PROP_FORM_ID, form.getId());
  Logger.log('フォームを作成しました: %s', form.getId());
  return form;
}

function getOrCreateSheet_(props, form) {
  const id = props.getProperty(PROP_SHEET_ID);
  if (id) {
    try {
      return SpreadsheetApp.openById(id);
    } catch (e) {
      Logger.log('保存されていたスプレッドシート（%s）を開けないので作り直します: %s', id, e);
    }
  }
  // フォーム側に既にリンク済みの回答先があればそれを使う
  const linked = currentDestinationId_(form);
  let ss;
  if (linked) {
    ss = SpreadsheetApp.openById(linked);
  } else {
    ss = SpreadsheetApp.create(CONFIG.sheetName);
    form.setDestination(FormApp.DestinationType.SPREADSHEET, ss.getId());
    Logger.log('回答スプレッドシートを作成してリンクしました: %s', ss.getId());
  }
  props.setProperty(PROP_SHEET_ID, ss.getId());
  return ss;
}

function currentDestinationId_(form) {
  try {
    return form.getDestinationId();
  } catch (e) {
    return null; // 回答先が未設定のときは例外になる
  }
}

/** 「自動返信ログ」シート。無ければ作り、見出し行を入れる。 */
function ensureLogSheet_(ss) {
  let sheet = ss.getSheetByName(CONFIG.logSheetName);
  if (!sheet) sheet = ss.insertSheet(CONFIG.logSheetName);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(LOG_HEADER);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function ensureSubmitTrigger_(form) {
  const exists = ScriptApp.getProjectTriggers().some(
    (t) =>
      t.getHandlerFunction() === TRIGGER_HANDLER &&
      t.getEventType() === ScriptApp.EventType.ON_FORM_SUBMIT &&
      t.getTriggerSourceId() === form.getId()
  );
  if (exists) return;
  ScriptApp.newTrigger(TRIGGER_HANDLER).forForm(form).onFormSubmit().create();
  Logger.log('送信時トリガーを登録しました: %s', TRIGGER_HANDLER);
}

/**
 * フォーム送信時に呼ばれる。回答は Google 側で回答スプレッドシートに自動記録される。
 * ここでは回答を取り出して AutoReply.gs の handleSubmission_ に渡す（分類 → 返信 → Issue → ログ → 運営者通知）。
 */
function onFormSubmit(e) {
  const response = e.response;
  const answers = {};
  response.getItemResponses().forEach((ir) => {
    answers[ir.getItem().getTitle()] = String(ir.getResponse() || '');
  });
  const submission = {
    receivedAt: response.getTimestamp(),
    name: (answers[Q_NAME] || '').trim(),
    // 返信先は入力欄「メールアドレス（返信先）」の値だけ（フォームのメールアドレス自動収集は使っていない）
    email: (answers[Q_EMAIL] || '').trim(),
    selectedCategory: answers[Q_CATEGORY] || '',
    body: answers[Q_BODY] || '',
  };
  handleSubmission_(submission);
}
