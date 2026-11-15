// 생성된 안드로이드 프로젝트에 카메라·진동 권한을 추가한다.
// (블루투스 권한은 bluetooth-le 플러그인이 자동으로 추가)
const fs = require("fs");
const path = require("path");

const manifestPath = path.join(__dirname, "..", "android", "app", "src", "main", "AndroidManifest.xml");
let xml = fs.readFileSync(manifestPath, "utf8");

const additions = [
  '<uses-permission android:name="android.permission.CAMERA" />',
  '<uses-permission android:name="android.permission.VIBRATE" />',
  '<uses-feature android:name="android.hardware.camera" android:required="false" />',
  '<uses-feature android:name="android.hardware.camera.flash" android:required="false" />',
  '<uses-feature android:name="android.hardware.bluetooth_le" android:required="false" />',
];

const missing = additions.filter(line => !xml.includes(line.split('"')[1]));
if (missing.length) {
  xml = xml.replace("<application", missing.join("\n    ") + "\n\n    <application");
  fs.writeFileSync(manifestPath, xml);
}
console.log("AndroidManifest 권한 추가:", missing.length ? missing : "이미 있음");
