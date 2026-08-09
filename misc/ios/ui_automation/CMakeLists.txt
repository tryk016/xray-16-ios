cmake_minimum_required(VERSION 3.25)

project(OpenXRayUIAutomation LANGUAGES C OBJC)

set(CMAKE_XCODE_ATTRIBUTE_CODE_SIGN_STYLE Automatic)
set(CMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED YES)
set(CMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_REQUIRED YES)
set(CMAKE_XCODE_ATTRIBUTE_CODE_SIGN_IDENTITY "Apple Development")
set(CMAKE_XCODE_ATTRIBUTE_DEVELOPMENT_TEAM RMJWWPF379)
set(CMAKE_XCODE_ATTRIBUTE_TARGETED_DEVICE_FAMILY 1)

add_executable(AutomationHost MACOSX_BUNDLE AutomationHost.m)
target_link_libraries(AutomationHost PRIVATE "-framework UIKit")
set_target_properties(AutomationHost PROPERTIES
    XCODE_GENERATE_SCHEME ON
    XCODE_ATTRIBUTE_GENERATE_INFOPLIST_FILE YES
    XCODE_ATTRIBUTE_PRODUCT_BUNDLE_IDENTIFIER io.github.tryk016.openxray.automation-host
)

add_library(OpenXRayUITests MODULE OpenXRayUITests.m)
target_link_libraries(OpenXRayUITests PRIVATE
    "-framework XCTest"
    "-framework XCUIAutomation"
    "-framework AVFAudio"
)
add_dependencies(OpenXRayUITests AutomationHost)
set_target_properties(OpenXRayUITests PROPERTIES
    BUNDLE ON
    BUNDLE_EXTENSION xctest
    XCODE_GENERATE_SCHEME ON
    XCODE_PRODUCT_TYPE com.apple.product-type.bundle.ui-testing
    XCODE_ATTRIBUTE_GENERATE_INFOPLIST_FILE YES
    XCODE_ATTRIBUTE_PRODUCT_BUNDLE_IDENTIFIER io.github.tryk016.openxray.ui-tests
    # Xcode 27's bundled XCTest/XCUIAutomation frameworks require iOS 17.0.
    # This is test-tooling-only and does not change OpenXRay's iOS 16.4 baseline.
    XCODE_ATTRIBUTE_IPHONEOS_DEPLOYMENT_TARGET 17.0
    XCODE_ATTRIBUTE_TEST_TARGET_NAME AutomationHost
)
