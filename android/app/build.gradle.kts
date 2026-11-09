import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

// 发布签名：android/keystore.properties（不进仓库）写 storeFile / storePassword / keyAlias / keyPassword
// 本 App 唯一会联系的项目服务器（心跳、托管、协助刷新、更新检查）。默认官网；
// 给 beta 站测试的预发布包：./gradlew assembleRelease -PkebiaoSite=https://beta.kebiao.nufe.wiki
val kebiaoSite = (findProperty("kebiaoSite") as String?)?.trimEnd('/') ?: "https://kebiao.nufe.wiki"
require(Regex("""^https://[a-z0-9.-]+$""").matches(kebiaoSite)) { "kebiaoSite 必须是 https://主机名" }

val keystore = rootProject.file("keystore.properties").takeIf { it.exists() }?.let { file ->
    Properties().apply { file.inputStream().use { load(it) } }
}

android {
    namespace = "wiki.nufe.kebiao"
    compileSdk = 37

    defaultConfig {
        applicationId = "wiki.nufe.kebiao"
        minSdk = 26
        targetSdk = 37
        versionCode = 29
        versionName = "2.2.0-beta.4"
        buildConfigField("String", "SITE", "\"$kebiaoSite\"")
    }

    signingConfigs {
        if (keystore != null) {
            create("release") {
                storeFile = rootProject.file(keystore.getProperty("storeFile"))
                storePassword = keystore.getProperty("storePassword")
                keyAlias = keystore.getProperty("keyAlias")
                keyPassword = keystore.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    androidResources {
        localeFilters += listOf("zh", "zh-rCN")
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2026.09.00")
    implementation(composeBom)
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.glance:glance-appwidget:1.2.0")
    implementation("androidx.glance:glance-material3:1.2.0")
    implementation("androidx.work:work-runtime-ktx:2.12.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
