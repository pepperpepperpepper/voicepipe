# Kotlin serialization keeps serializer classes alive via reflection.
-keepattributes RuntimeVisibleAnnotations,AnnotationDefault

# Keep @Serializable companion serializer instances.
-keep,includedescriptorclasses class dev.voicepipe.zwangli.**$$serializer { *; }
-keepclassmembers class dev.voicepipe.zwangli.** {
    *** Companion;
}
-keepclasseswithmembers class dev.voicepipe.zwangli.** {
    kotlinx.serialization.KSerializer serializer(...);
}
