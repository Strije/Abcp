# Сжатие release-сборки (R8). Наш код НЕ переименовываем: Gson разбирает ответы ABCP/Laximo/сервера
# по именам полей data-классов, а ленты/кэши сохраняются тем же Gson. Сжимаются библиотеки.
-keep class com.example.myapplication.** { *; }
-keepattributes Signature, InnerClasses, EnclosingMethod, *Annotation*, SourceFile, LineNumberTable

# Gson: TypeToken с дженериками (списки в ленте уведомлений, кэши)
-keep class com.google.gson.reflect.TypeToken { *; }
-keep class * extends com.google.gson.reflect.TypeToken

# Retrofit + корутины: интерфейсы API и Response с дженериками
-keep,allowobfuscation,allowshrinking interface retrofit2.Call
-keep,allowobfuscation,allowshrinking class retrofit2.Response
-keep,allowobfuscation,allowshrinking class kotlin.coroutines.Continuation

# Отчёты о сбоях в AppMetrica — с номерами строк, но без путей исходников
-renamesourcefileattribute SourceFile
