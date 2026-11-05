# org.json / Glance / WorkManager 自带混淆规则，这里不需要额外配置。

# Glance 通过类名解析 AppWidget 子类；R8 合并/重命名后周组件会被当成今天组件渲染。
# https://issuetracker.google.com/issues/371791302
-keep class * extends androidx.glance.appwidget.GlanceAppWidget { *; }
-keep class * extends androidx.glance.appwidget.GlanceAppWidgetReceiver { *; }
