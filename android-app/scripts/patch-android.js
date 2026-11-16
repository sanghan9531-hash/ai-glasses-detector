// 생성된 안드로이드 프로젝트를 보완한다.
//  1) 카메라·진동 권한 추가 (블루투스 권한은 bluetooth-le 플러그인이 자동으로 추가)
//  2) 자기장(자석) 센서 플러그인 추가 및 등록
const fs = require("fs");
const path = require("path");

const appDir = path.join(__dirname, "..", "android", "app", "src", "main");
const config = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "capacitor.config.json"), "utf8"));
const pkg = config.appId;                                   // 예: io.github.sanghan9531.aiglasses
const javaDir = path.join(appDir, "java", ...pkg.split("."));

// ── 1) 권한 ─────────────────────────────────────────────
const manifestPath = path.join(appDir, "AndroidManifest.xml");
let xml = fs.readFileSync(manifestPath, "utf8");
const additions = [
  '<uses-permission android:name="android.permission.CAMERA" />',
  '<uses-permission android:name="android.permission.VIBRATE" />',
  '<uses-feature android:name="android.hardware.camera" android:required="false" />',
  '<uses-feature android:name="android.hardware.camera.flash" android:required="false" />',
  '<uses-feature android:name="android.hardware.bluetooth_le" android:required="false" />',
  '<uses-feature android:name="android.hardware.sensor.compass" android:required="false" />',
];
const missing = additions.filter(line => !xml.includes(line.split('"')[1]));
if (missing.length) {
  xml = xml.replace("<application", missing.join("\n    ") + "\n\n    <application");
  fs.writeFileSync(manifestPath, xml);
}
console.log("AndroidManifest 권한 추가:", missing.length ? missing : "이미 있음");

// ── 2) 자기장 센서 플러그인 ──────────────────────────────
fs.mkdirSync(javaDir, { recursive: true });

fs.writeFileSync(path.join(javaDir, "MagnetometerPlugin.java"), `package ${pkg};

import android.content.Context;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/** 휴대폰 자기장 센서 값(μT)을 웹 화면으로 보내는 플러그인 */
@CapacitorPlugin(name = "Magnetometer")
public class MagnetometerPlugin extends Plugin implements SensorEventListener {
    private SensorManager sensorManager;
    private Sensor sensor;
    private long lastSent = 0;

    @Override
    public void load() {
        sensorManager = (SensorManager) getContext().getSystemService(Context.SENSOR_SERVICE);
        sensor = sensorManager != null ? sensorManager.getDefaultSensor(Sensor.TYPE_MAGNETIC_FIELD) : null;
    }

    @PluginMethod
    public void isAvailable(PluginCall call) {
        JSObject result = new JSObject();
        result.put("available", sensor != null);
        call.resolve(result);
    }

    @PluginMethod
    public void start(PluginCall call) {
        if (sensor == null) {
            call.reject("이 기기에는 자기장 센서가 없습니다.");
            return;
        }
        sensorManager.registerListener(this, sensor, SensorManager.SENSOR_DELAY_GAME);
        call.resolve();
    }

    @PluginMethod
    public void stop(PluginCall call) {
        if (sensorManager != null) sensorManager.unregisterListener(this);
        call.resolve();
    }

    @Override
    public void onSensorChanged(SensorEvent event) {
        long now = System.currentTimeMillis();
        if (now - lastSent < 50) return;          // 초당 약 20회
        lastSent = now;
        JSObject data = new JSObject();
        data.put("x", (double) event.values[0]);
        data.put("y", (double) event.values[1]);
        data.put("z", (double) event.values[2]);
        notifyListeners("reading", data);
    }

    @Override
    public void onAccuracyChanged(Sensor s, int accuracy) { }

    @Override
    protected void handleOnDestroy() {
        if (sensorManager != null) sensorManager.unregisterListener(this);
    }
}
`);

fs.writeFileSync(path.join(javaDir, "ScreenGuardPlugin.java"), `package ${pkg};

import android.app.Activity;
import android.view.Window;
import android.view.WindowManager;

import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/** 감시 중 화면이 꺼지지 않게 하고, 화면 밝기를 낮추거나 되돌리는 플러그인 */
@CapacitorPlugin(name = "ScreenGuard")
public class ScreenGuardPlugin extends Plugin {

    @PluginMethod
    public void keepScreenOn(PluginCall call) {
        final boolean on = Boolean.TRUE.equals(call.getBoolean("on", true));
        final Activity activity = getActivity();
        if (activity == null) { call.reject("화면을 제어할 수 없습니다."); return; }
        activity.runOnUiThread(() -> {
            Window window = activity.getWindow();
            if (on) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            call.resolve();
        });
    }

    /** value: 0.01 ~ 1.0 = 앱 화면 밝기, -1 = 휴대폰 설정 밝기로 되돌리기 */
    @PluginMethod
    public void setBrightness(PluginCall call) {
        Double value = call.getDouble("value", -1.0);
        final float v = value == null ? -1f : (float) Math.max(-1.0, Math.min(1.0, value));
        final Activity activity = getActivity();
        if (activity == null) { call.reject("화면을 제어할 수 없습니다."); return; }
        activity.runOnUiThread(() -> {
            Window window = activity.getWindow();
            WindowManager.LayoutParams lp = window.getAttributes();
            lp.screenBrightness = v < 0 ? WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE : Math.max(0.01f, v);
            window.setAttributes(lp);
            call.resolve();
        });
    }
}
`);

fs.writeFileSync(path.join(javaDir, "MainActivity.java"), `package ${pkg};

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(MagnetometerPlugin.class);   // 자기장 센서 플러그인 등록
        registerPlugin(ScreenGuardPlugin.class);    // 화면 유지·밝기 플러그인 등록
        super.onCreate(savedInstanceState);
    }
}
`);
console.log("자기장 센서·화면 유지 플러그인 추가:", javaDir);

// ── 3) 앱 아이콘·시작 화면 ───────────────────────────────
const resSrc = path.join(__dirname, "..", "resources", "res");
if (fs.existsSync(resSrc)) {
  fs.cpSync(resSrc, path.join(appDir, "res"), { recursive: true, force: true });
  const bgFile = path.join(appDir, "res", "values", "ic_launcher_background.xml");
  fs.mkdirSync(path.dirname(bgFile), { recursive: true });
  fs.writeFileSync(bgFile, '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n' +
    '    <color name="ic_launcher_background">#172554</color>\n</resources>\n');
  console.log("앱 아이콘·시작 화면 교체 완료");
}

// ── 4) 플레이스토어 요구 사항: Android 16 (API 36) 대상 ───
const androidDir = path.join(__dirname, "..", "android");
const varsPath = path.join(androidDir, "variables.gradle");
if (fs.existsSync(varsPath)) {
  let v = fs.readFileSync(varsPath, "utf8");
  v = v.replace(/(compileSdkVersion\s*=\s*)\d+/, "$136").replace(/(targetSdkVersion\s*=\s*)\d+/, "$136");
  fs.writeFileSync(varsPath, v);
  console.log("compileSdk/targetSdk → 36");
}
const propsPath = path.join(androidDir, "gradle.properties");
if (fs.existsSync(propsPath)) {
  let gp = fs.readFileSync(propsPath, "utf8");
  if (!gp.includes("android.suppressUnsupportedCompileSdk")) {
    fs.writeFileSync(propsPath, gp.trimEnd() + "\nandroid.suppressUnsupportedCompileSdk=36\n");
  }
}

// ── 5) 출시용 버전 번호 (출시 빌드에서만 환경변수로 전달) ──
const buildGradle = path.join(androidDir, "app", "build.gradle");
if (process.env.VERSION_CODE && fs.existsSync(buildGradle)) {
  let g = fs.readFileSync(buildGradle, "utf8");
  g = g.replace(/versionCode\s+\d+/, "versionCode " + parseInt(process.env.VERSION_CODE, 10));
  if (process.env.VERSION_NAME) g = g.replace(/versionName\s+"[^"]*"/, `versionName "${process.env.VERSION_NAME}"`);
  fs.writeFileSync(buildGradle, g);
  console.log("버전:", process.env.VERSION_CODE, process.env.VERSION_NAME || "");
}
