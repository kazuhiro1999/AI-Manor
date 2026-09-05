/* manor web — 共通の画面見出し（ADR-010 D7）。
 * 「どの画面にも先頭に『ここで何をするか』」。画面名（大きく）と一行の説明（何をする場所か）を
 * 全モジュールの先頭に置く。README を読ませない（ADR-009 D7 と同じ姿勢——その場に書く）。
 * login・setup モジュール自体の入口（.login-card / .setup-card の中の <h1>）は自前の
 * シェルを持つのでこの部品は使わず、見出しをその場に書く（ModuleDefinition の
 * title/description 自体は登録簿の検算のために両方とも持つ）。setup ウィザードの
 * 各段（§3「段の題＋一行」）はこの部品をそのまま再利用する。
 */
export function ScreenHeader({ title, description }: { title: string; description: string }) {
  return (
    <div className="screen-header">
      <h2>{title}</h2>
      <p className="panel-note">{description}</p>
    </div>
  );
}
