pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}
plugins {
    id("org.gradle.toolchains.foojay-resolver-convention") version "1.0.0"
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // RuStore SDK (push). Новый адрес — старый artifactory-external.vkpartner.ru отключают 30.09.2026
        maven { url = uri("https://nexus-external.rustore.ru/repository/maven-rustore-exposed/") }
    }
}

rootProject.name = "Avtodrug"
include(":app")
