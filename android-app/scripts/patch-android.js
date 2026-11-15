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

fs.writeFileSync(path.join(javaDir, "MainActivity.java"), `package ${pkg};

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(MagnetometerPlugin.class);   // 자기장 센서 플러그인 등록
        super.onCreate(savedInstanceState);
    }
}
`);
console.log("자기장 센서 플러그인 추가:", javaDir);
