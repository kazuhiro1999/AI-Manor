/* manor web — 担当カードの姿（VRM）を1フレームだけ静止画に焼く（ADR-011 D3 追補）。
 *
 * three / @pixiv/three-vrm は `web/package.json` の dependencies には無い。だが実体は
 * バックエンドがすでに `/face-static/vendor/...` として（認証不要で）配っている——小窓
 * （`src/manor/web/face_static/face.html`）が読んでいるのと同じ資産。**SPA の index.html に
 * import map を置いた**ので（`web/index.html`）、ここは実行時の動的 `import()` で素直に読める。
 * バンドルには入らない（`@vite-ignore`）ので、依存もビルドサイズも増えない。
 *
 * 以前はソースを fetch して bare specifier を文字列置換し Blob 化して読んでいたが、
 * **実機で `ERR_NAME_NOT_RESOLVED` になり姿が1枚も出なかった**（2026-09-04 実測）。
 * 標準の仕組み（import map）に寄せたほうが短く、vendor を更新しても壊れにくい。
 *
 * 1担当ぶんの処理（`renderFaceThumbnail`）は「読み込み→1フレーム描画→toDataURL→
 * レンダラー破棄」を1つの async 関数にまとめてある。呼び出し側（`index.tsx`）が
 * これを1体ずつ順番に await することで、同時に存在する WebGL コンテキストは常に1つに絞る。
 */

// three.js/three-vrm の型はここでは持ち込まない（vendor が生の ES module で型定義を
// 持たないため）。渡す・受け取るデータの形だけ最小限に自前で書く。
type VendorModule = Record<string, unknown>;

const VENDOR_BASE = "/face-static/vendor";
const THREE_URL = new URL(`${VENDOR_BASE}/three.module.js`, window.location.origin).href;

/** 小窓が使う vendor のうち、ここで要るものだけ。**vendor は型定義を持たない**ので
 * 生のコンストラクタとして受け取り、使う側で最小限に扱う。 */
interface FaceVendorModules {
  THREE: VendorModule;
  // vendor は型定義を持たないので、ここは**意図的に緩い**——このファイルの中でだけ使い、
  // 使い方は小窓（face.html）の写しなので、型で縛るより実物に合わせるほうが安全。
  GLTFLoader: new () => {
    register: (plugin: (parser: unknown) => unknown) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    loadAsync: (url: string) => Promise<any>;
  };
  VRMLoaderPlugin: new (parser: unknown) => unknown;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  VRMUtils: any;
}

/** 一度読んだら使い回す（担当が7人いても vendor の取得は1回）。 */
let vendorModulesPromise: Promise<FaceVendorModules> | null = null;

/** 小窓と同じ vendor 一式を読み込む（初回だけ実際にネットワークへ行く。以降はキャッシュ）。 */
function loadFaceVendorModules(): Promise<FaceVendorModules> {
  if (!vendorModulesPromise) {
    vendorModulesPromise = (async () => {
      // import map（`web/index.html`）が bare の "three" を解決するので、vendor を
      // そのまま読める。書き換えも Blob も要らない。
      const [THREE, gltfMod, vrmMod] = (await Promise.all([
        import(/* @vite-ignore */ THREE_URL),
        import(/* @vite-ignore */ `${VENDOR_BASE}/loaders/GLTFLoader.js`),
        import(/* @vite-ignore */ `${VENDOR_BASE}/three-vrm.module.js`),
      ])) as [VendorModule, Record<string, unknown>, Record<string, unknown>];
      return {
        THREE,
        GLTFLoader: gltfMod.GLTFLoader as FaceVendorModules["GLTFLoader"],
        VRMLoaderPlugin: vrmMod.VRMLoaderPlugin as FaceVendorModules["VRMLoaderPlugin"],
        VRMUtils: vrmMod.VRMUtils as FaceVendorModules["VRMUtils"],
      };
    })();
  }
  return vendorModulesPromise;
}

// ---- VRM の最小限の形（face.html が触っている範囲だけ） ------------------------------
interface VrmBoneNode {
  rotation: { x: number; y: number; z: number };
  getWorldPosition(target: { x: number; y: number; z: number }): void;
}
interface VrmLike {
  scene: unknown;
  humanoid: { getNormalizedBoneNode(name: string): VrmBoneNode | null };
  expressionManager?: { setValue(name: string, weight: number): void };
  update(dt: number): void;
}

export type FaceRenderResult = { status: "loaded"; dataUrl: string } | { status: "error" };

// カードに収める1枚の大きさ（頭部に寄せて描く。小窓の280x340より小さいクロップ）。
const THUMB_WIDTH = 160;
const THUMB_HEIGHT = 200;

// 立ちポーズ（`face_static/face.html` の restPose と同じ値）。
const REST_POSE = { upperArmDown: 1.44, upperArmIn: 0.1, lowerArmBend: 0.3, handIn: 0.15 };

interface RendererLike {
  dispose(): void;
  forceContextLoss(): void;
  render(scene: unknown, camera: unknown): void;
  setPixelRatio(v: number): void;
  setSize(w: number, h: number, updateStyle?: boolean): void;
}

interface ThreeNamespace {
  WebGLRenderer: new (opts: Record<string, unknown>) => RendererLike;
  Scene: new () => { add(obj: unknown): void };
  PerspectiveCamera: new (
    fov: number,
    aspect: number,
    near: number,
    far: number
  ) => {
    fov: number;
    position: { set(x: number, y: number, z: number): void };
    lookAt(x: number, y: number, z: number): void;
    updateProjectionMatrix(): void;
  };
  AmbientLight: new (color: number, intensity: number) => unknown;
  DirectionalLight: new (color: number, intensity: number) => { position: { set(x: number, y: number, z: number): void } };
  Vector3: new () => { x: number; y: number; z: number };
  Box3: new () => { setFromObject(obj: unknown): { max: { y: number }; min: { y: number } } };
}

/** 1担当ぶんの姿を読み込み、頭部に寄せた1フレームだけを PNG data URL に焼いて返す。
 * 成功・失敗どちらでも WebGL コンテキストは必ずこの関数の中で破棄する
 * （呼び出し側はこれを1体ずつ await すれば、同時に生きる GL コンテキストは常に1つ）。 */
export async function renderFaceThumbnail(agentId: string): Promise<FaceRenderResult> {
  let renderer: RendererLike | null = null;
  try {
    const { THREE, GLTFLoader, VRMLoaderPlugin, VRMUtils } = await loadFaceVendorModules();

    const canvas = document.createElement("canvas");
    canvas.width = THUMB_WIDTH;
    canvas.height = THUMB_HEIGHT;

    const ThreeCtor = THREE as unknown as ThreeNamespace;

    renderer = new ThreeCtor.WebGLRenderer({ canvas, alpha: true, antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(1);
    renderer.setSize(THUMB_WIDTH, THUMB_HEIGHT, false);

    const scene = new ThreeCtor.Scene();
    const camera = new ThreeCtor.PerspectiveCamera(24, THUMB_WIDTH / THUMB_HEIGHT, 0.1, 20);
    scene.add(new ThreeCtor.AmbientLight(0xffffff, 1.5));
    const key = new ThreeCtor.DirectionalLight(0xffffff, 1.1);
    key.position.set(0.6, 1.4, 1.2);
    scene.add(key);

    const loader = new GLTFLoader();
    loader.register((parser: unknown) => new VRMLoaderPlugin(parser));

    const gltf = await loader.loadAsync(`/face/model.vrm?agent=${encodeURIComponent(agentId)}`);
    const vrm = gltf.userData.vrm;
    if (!vrm) return { status: "error" };

    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    VRMUtils.combineSkeletons(gltf.scene);
    (vrm.scene as { traverse(cb: (o: { frustumCulled: boolean }) => void): void }).traverse((o) => {
      o.frustumCulled = false;
    });
    scene.add(vrm.scene);

    // 立ちポーズ（face.html の restPose と同じ）
    const h = vrm.humanoid;
    const g = (n: string) => h.getNormalizedBoneNode(n);
    const lu = g("leftUpperArm");
    const ru = g("rightUpperArm");
    const ll = g("leftLowerArm");
    const rl = g("rightLowerArm");
    const lh = g("leftHand");
    const rh = g("rightHand");
    if (lu) {
      lu.rotation.z = -REST_POSE.upperArmDown;
      lu.rotation.x = REST_POSE.upperArmIn;
    }
    if (ru) {
      ru.rotation.z = REST_POSE.upperArmDown;
      ru.rotation.x = REST_POSE.upperArmIn;
    }
    if (ll) ll.rotation.y = -REST_POSE.lowerArmBend;
    if (rl) rl.rotation.y = REST_POSE.lowerArmBend;
    if (lh) lh.rotation.y = -REST_POSE.handIn;
    if (rh) rh.rotation.y = REST_POSE.handIn;

    vrm.update(0.016);

    // 頭部に寄せる framing（face.html boot() 後半と同じ計算）。
    const head = h.getNormalizedBoneNode("head");
    const p = new ThreeCtor.Vector3();
    if (head) head.getWorldPosition(p);
    else {
      p.x = 0;
      p.y = 1.4;
      p.z = 0;
    }

    const box = new ThreeCtor.Box3().setFromObject(vrm.scene);
    const height = Math.max(0.5, box.max.y - box.min.y);
    const top = box.max.y + 0.04;
    const bottom = box.max.y - height * 0.32;
    const half = Math.max(0.15, (top - bottom) / 2);
    const fovRad = (camera.fov * Math.PI) / 180;
    const aimY = (top + bottom) / 2;
    const camZ = p.z + half / Math.tan(fovRad / 2);
    camera.position.set(0, aimY, camZ);
    camera.lookAt(0, aimY, p.z);
    camera.updateProjectionMatrix();

    // 伏し目にする（[[執事の外見仕様]]「正面を見ない」。face.html と同じ）
    const neck = h.getNormalizedBoneNode("neck");
    if (neck) neck.rotation.x += 0.1;
    if (head) head.rotation.x += 0.06;
    if (vrm.expressionManager) vrm.expressionManager.setValue("neutral", 1.0);

    // 上の首・頭の角度変更をスキニングへ反映させてから描く（face.html はこの後 loop() の
    // 次フレームで反映されるが、ここは1フレームしか描かないので明示的にもう一度呼ぶ）。
    vrm.update(0);

    renderer.render(scene, camera);
    const dataUrl = canvas.toDataURL("image/png");
    return { status: "loaded", dataUrl };
  } catch {
    return { status: "error" };
  } finally {
    if (renderer) {
      try {
        renderer.dispose();
      } catch {
        /* 破棄に失敗しても実害は無い（ページにも残さない） */
      }
      try {
        renderer.forceContextLoss();
      } catch {
        /* 同上 */
      }
    }
  }
}
