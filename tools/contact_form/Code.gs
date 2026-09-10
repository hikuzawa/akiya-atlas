/**
 * 空き家アトラス お問い合わせフォーム（Google Apps Script）。
 *
 * setup() を一度実行すると、次をまとめて行う:
 *   1. Google フォームの作成（質問: お名前 / メールアドレス / 種別（選択）/ 内容）
 *   2. メールアドレス収集の有効化
 *   3. 回答先スプレッドシートの作成とフォームへのリンク
 *   4. フォーム送信時トリガー（onFormSubmit）の登録
 *   5. 完了時にフォーム URL（回答用・編集用）と回答スプレッドシートの URL をログに出力
 *
 * 再実行しても二重には作らない。作成済みの ID をスクリプトプロパティに保存し、無いものだけ作る。
 * 種別や通知先を変えるときは CONFIG を書き換えて setup() を再実行する（既存フォームの質問は変えないので、
 * 質問を作り直したいときは reset() で ID を忘れさせてから setup() を実行する。フォーム自体は削除しない）。
 */

const CONFIG = {
  formTitle: '空き家アトラス お問い合わせ',
  formDescription:
    '掲載内容の訂正・削除のご依頼、自治体・移住推進組織からのご連絡、その他のお問い合わせはこちらから。' +
    '物件の紹介・仲介は行っていません。回答には数日いただく場合があります。',
  // 種別（選択肢）。順番どおりに表示される
  categories: [
    '掲載内容の訂正・削除の依頼',
    '自治体・移住推進組織からのご連絡',
    '空き家の所有者からのご相談',
    '取材・提携のご相談',
    'その他',
  ],
  sheetName: '空き家アトラス お問い合わせ（回答）',
  // 通知先メール。空文字ならスクリプトを実行したアカウント宛てに送る
  notifyTo: '',
  confirmationMessage: 'お問い合わせを受け付けました。内容を確認のうえ、必要に応じてご連絡します。',
};

const PROP_FORM_ID = 'CONTACT_FORM_ID';
const PROP_SHEET_ID = 'CONTACT_SHEET_ID';
const TRIGGER_HANDLER = 'onFormSubmit';
const Q_NAME = 'お名前';
const Q_EMAIL = 'メールアドレス（返信先）';
const Q_CATEGORY = '種別';
const Q_BODY = '内容';

/** すべてを一度に用意する。完了するとフォームの回答用 URL を返し、ログにも出す。 */
function setup() {
  const props = PropertiesService.getScriptProperties();
  const form = getOrCreateForm_(props);
  const sheet = getOrCreateSheet_(props, form);
  ensureSubmitTrigger_(form);
  Logger.log('フォーム（回答用 URL）: %s', form.getPublishedUrl());
  Logger.log('フォーム（編集用 URL）: %s', form.getEditUrl());
  Logger.log('回答スプレッドシート: %s', sheet.getUrl());
  Logger.log('送信時トリガー: %s（登録済み）', TRIGGER_HANDLER);
  return form.getPublishedUrl();
}

/** 保存した ID を忘れる（フォームやシートは削除しない）。次の setup() で新しく作る。 */
function reset() {
  const props = PropertiesService.getScriptProperties();
  props.deleteProperty(PROP_FORM_ID);
  props.deleteProperty(PROP_SHEET_ID);
  Logger.log('保存していた ID を消しました。次の setup() で新しいフォームとシートを作ります。');
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
  form.setCollectEmail(true); // メールアドレス収集を有効化（回答にメールアドレス列が付く）
  form.setLimitOneResponsePerUser(false);
  form.setAllowResponseEdits(false);
  form.setConfirmationMessage(CONFIG.confirmationMessage);

  form.addTextItem().setTitle(Q_NAME).setRequired(true);
  form
    .addTextItem()
    .setTitle(Q_EMAIL)
    .setHelpText('こちらに返信します。')
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
 * フォーム送信時に呼ばれる。回答は Google 側で回答スプレッドシートに自動記録されるので、
 * ここでは運営者への通知メールだけを送る（返信先は回答者のメールアドレス）。
 */
function onFormSubmit(e) {
  const response = e.response;
  const answers = {};
  response.getItemResponses().forEach((ir) => {
    answers[ir.getItem().getTitle()] = String(ir.getResponse() || '');
  });
  const respondent = response.getRespondentEmail() || answers[Q_EMAIL] || '';
  const category = answers[Q_CATEGORY] || '種別なし';
  const name = answers[Q_NAME] || '名前なし';
  const lines = Object.keys(answers).map((k) => k + ': ' + answers[k]);
  const sheetId = PropertiesService.getScriptProperties().getProperty(PROP_SHEET_ID);
  const sheetUrl = sheetId ? 'https://docs.google.com/spreadsheets/d/' + sheetId : '(未設定)';

  const subject = '[空き家アトラス] お問い合わせ: ' + category + '（' + name + '）';
  const body = ['受付日時: ' + response.getTimestamp(), '回答者メール: ' + respondent, '']
    .concat(lines)
    .concat(['', '回答一覧: ' + sheetUrl])
    .join('\n');

  const mail = {
    to: CONFIG.notifyTo || Session.getEffectiveUser().getEmail(),
    subject: subject,
    body: body,
  };
  if (respondent) mail.replyTo = respondent;
  MailApp.sendEmail(mail);
}
