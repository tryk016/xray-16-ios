#import <XCTest/XCTest.h>
#import <XCUIAutomation/XCUIAutomation.h>
#import <AVFAudio/AVFAudio.h>

#import <math.h>

@interface OpenXRayUITests : XCTestCase
@property(nonatomic, strong) AVAudioSession* interruptionAudioSession;
@property(nonatomic, strong) AVAudioEngine* interruptionAudioEngine;
@property(nonatomic, strong) AVAudioPlayerNode* interruptionAudioPlayer;
@property(nonatomic, strong) AVAudioPCMBuffer* interruptionAudioBuffer;
@end

@implementation OpenXRayUITests
- (void)tearDown
{
    [self stopForegroundAudioInterruptionDriver];
    [super tearDown];
}

- (XCUIApplication*)openXRayApplication
{
    return [[XCUIApplication alloc]
        initWithBundleIdentifier:@"io.github.tryk016.openxray.RMJWWPF379"];
}

- (XCUIApplication*)defaultBrowserApplication
{
    return [[XCUIApplication alloc] initWithBundleIdentifier:@"org.mozilla.ios.Firefox"];
}

- (BOOL)requireApplication:(XCUIApplication*)app
             reachesState:(XCUIApplicationState)state
                   timeout:(NSTimeInterval)timeout
                     stage:(NSString*)stage
{
    if ([app waitForState:state timeout:timeout])
        return YES;

    XCTFail(@"%@ did not reach state %ld (actual %ld)", stage,
        (long)state, (long)app.state);
    return NO;
}

- (BOOL)requireApplicationBackgrounded:(XCUIApplication*)app
                       applicationName:(NSString*)applicationName
                               timeout:(NSTimeInterval)timeout
                                 stage:(NSString*)stage
{
    const NSTimeInterval deadline = NSProcessInfo.processInfo.systemUptime + timeout;
    while (NSProcessInfo.processInfo.systemUptime < deadline)
    {
        const XCUIApplicationState state = app.state;
        if (state == XCUIApplicationStateRunningBackground ||
            state == XCUIApplicationStateRunningBackgroundSuspended)
            return YES;
        if (state == XCUIApplicationStateNotRunning)
        {
            XCTFail(@"%@ terminated %@", stage, applicationName);
            return NO;
        }
        [NSThread sleepForTimeInterval:0.1];
    }

    XCTFail(@"%@ did not background %@ (actual %ld)", stage, applicationName, (long)app.state);
    return NO;
}

- (void)attachScreenNamed:(NSString*)name
{
    XCTAttachment* attachment = [XCTAttachment attachmentWithScreenshot:XCUIScreen.mainScreen.screenshot];
    attachment.name = name;
    attachment.lifetime = XCTAttachmentLifetimeKeepAlways;
    [self addAttachment:attachment];
}

- (BOOL)prepareOpenXRay:(XCUIApplication*)app pause:(NSTimeInterval)pause stage:(NSString*)stage
{
    [app activate];
    if (![self requireApplication:app
                    reachesState:XCUIApplicationStateRunningForeground
                          timeout:30.0
                            stage:stage])
        return NO;
    [NSThread sleepForTimeInterval:pause];
    return YES;
}

- (BOOL)runFiveAppSwitchCyclesForOpenXRay:(XCUIApplication*)app attachmentPrefix:(NSString*)attachmentPrefix
{
    if (![self prepareOpenXRay:app pause:12.0 stage:@"initial OpenXRay foreground"])
        return NO;

    for (NSUInteger cycle = 1; cycle <= 5; ++cycle)
    {
        XCUIApplication* const browser = [self defaultBrowserApplication];
        NSURL* const lifecycleURL = [NSURL URLWithString:[NSString stringWithFormat:
            @"http://127.0.0.1:9/openxray-lifecycle?cycle=%lu", (unsigned long)cycle]];
        if (lifecycleURL == nil)
        {
            XCTFail(@"Unable to construct lifecycle URL for cycle %lu", (unsigned long)cycle);
            return NO;
        }

        [[XCUIDevice sharedDevice].system openURL:lifecycleURL];
        NSString* const browserForegroundStage = [NSString stringWithFormat:@"Default browser (Firefox) foreground in app-switch cycle %lu",
            (unsigned long)cycle];
        if (![self requireApplication:browser
                        reachesState:XCUIApplicationStateRunningForeground
                              timeout:30.0
                                stage:browserForegroundStage])
            return NO;

        [NSThread sleepForTimeInterval:1.0];
        NSString* const recoveryStage = [NSString stringWithFormat:@"OpenXRay recovery in app-switch cycle %lu",
            (unsigned long)cycle];
        [app activate];
        if (![self requireApplication:app
                        reachesState:XCUIApplicationStateRunningForeground
                              timeout:30.0
                                stage:recoveryStage])
            return NO;

        [NSThread sleepForTimeInterval:3.0];
        [self attachScreenNamed:[NSString stringWithFormat:@"%@-cycle-%lu",
            attachmentPrefix, (unsigned long)cycle]];
    }
    return YES;
}

- (void)stopForegroundAudioInterruptionDriver
{
    [self.interruptionAudioPlayer stop];
    [self.interruptionAudioEngine stop];

    if (self.interruptionAudioSession)
    {
        NSError* error = nil;
        if (![self.interruptionAudioSession setActive:NO
                                          withOptions:AVAudioSessionSetActiveOptionNotifyOthersOnDeactivation
                                               error:&error])
        {
            NSLog(@"OpenXRay UI automation: audio-session deactivation failed: %@", error);
        }
    }

    self.interruptionAudioBuffer = nil;
    self.interruptionAudioPlayer = nil;
    self.interruptionAudioEngine = nil;
    self.interruptionAudioSession = nil;
}

- (BOOL)startForegroundAudioInterruptionDriver
{
    AVAudioSession* const session = AVAudioSession.sharedInstance;
    NSError* error = nil;
    if (![session setCategory:AVAudioSessionCategoryPlayback
                   withOptions:0
                        error:&error])
    {
        XCTFail(@"Could not set test-runner AVAudioSession Playback category: %@", error);
        return NO;
    }
    if (![session setActive:YES error:&error])
    {
        XCTFail(@"Could not activate test-runner AVAudioSession: %@", error);
        return NO;
    }

    AVAudioFormat* const format = [[AVAudioFormat alloc] initStandardFormatWithSampleRate:48000.0
                                                                                   channels:1];
    AVAudioPCMBuffer* const buffer = [[AVAudioPCMBuffer alloc] initWithPCMFormat:format
                                                                     frameCapacity:4800];
    buffer.frameLength = buffer.frameCapacity;
    float* const samples = buffer.floatChannelData[0];
    for (AVAudioFrameCount frame = 0; frame < buffer.frameLength; ++frame)
        samples[frame] = 0.1f * sinf((2.0f * (float)M_PI * 440.0f * frame) / format.sampleRate);

    AVAudioEngine* const engine = [[AVAudioEngine alloc] init];
    AVAudioPlayerNode* const player = [[AVAudioPlayerNode alloc] init];
    [engine attachNode:player];
    [engine connect:player to:engine.mainMixerNode format:format];
    [player scheduleBuffer:buffer
                    atTime:nil
                   options:AVAudioPlayerNodeBufferLoops
         completionHandler:nil];
    [engine prepare];
    if (![engine startAndReturnError:&error])
    {
        XCTFail(@"Could not start test-runner AVAudioEngine: %@", error);
        [session setActive:NO withOptions:AVAudioSessionSetActiveOptionNotifyOthersOnDeactivation error:nil];
        return NO;
    }
    if (!engine.isRunning)
    {
        XCTFail(@"Test-runner AVAudioEngine reported success but is not running");
        [engine stop];
        [session setActive:NO withOptions:AVAudioSessionSetActiveOptionNotifyOthersOnDeactivation error:nil];
        return NO;
    }
    [player play];
    if (!player.isPlaying)
    {
        XCTFail(@"Test-runner AVAudioPlayerNode did not start playback");
        [engine stop];
        [session setActive:NO withOptions:AVAudioSessionSetActiveOptionNotifyOthersOnDeactivation error:nil];
        return NO;
    }

    self.interruptionAudioSession = session;
    self.interruptionAudioEngine = engine;
    self.interruptionAudioPlayer = player;
    self.interruptionAudioBuffer = buffer;
    return YES;
}

- (void)testMainMenuToOptionsThreeTimes
{
    XCUIApplication* app = [self openXRayApplication];
    [self addTeardownBlock:^{
        [app terminate];
    }];
    for (NSUInteger attempt = 1; attempt <= 3; ++attempt)
    {
        [app activate];
        XCTAssertTrue([app waitForState:XCUIApplicationStateRunningForeground timeout:30.0]);
        [NSThread sleepForTimeInterval:6.0];
        [self attachScreenNamed:[NSString stringWithFormat:@"attempt-%lu-before", (unsigned long)attempt]];

        // The calibrated Credits point is (230/932, 276/430). Options is one
        // main-menu row above it in the landscape app coordinate space.
        XCUICoordinate* options = [app coordinateWithNormalizedOffset:
            CGVectorMake(230.0 / 932.0, 250.0 / 430.0)];
        [options tap];
        [NSThread sleepForTimeInterval:0.7];
        [options tap];
        [NSThread sleepForTimeInterval:3.0];
        [self attachScreenNamed:[NSString stringWithFormat:@"attempt-%lu-after", (unsigned long)attempt]];

        [app terminate];
    }
}

- (void)testLifecycleFiveAppSwitchCycles
{
    XCUIApplication* app = [self openXRayApplication];
    [self runFiveAppSwitchCyclesForOpenXRay:app attachmentPrefix:@"lifecycle"];
}

- (void)testAudioInterruptionWhileOpenXRayForeground
{
    XCUIApplication* app = [self openXRayApplication];

    if (![self prepareOpenXRay:app pause:8.0 stage:@"initial OpenXRay foreground for audio"])
        return;
    [self attachScreenNamed:@"audio-before-foreground-interruption"];
    if (![self startForegroundAudioInterruptionDriver])
        return;
    if (![self requireApplication:app
                    reachesState:XCUIApplicationStateRunningForeground
                          timeout:10.0
                            stage:@"OpenXRay foreground while test-runner audio is active"])
        return;

    [NSThread sleepForTimeInterval:3.0];
    [self stopForegroundAudioInterruptionDriver];
    if (![self requireApplication:app
                    reachesState:XCUIApplicationStateRunningForeground
                          timeout:30.0
                            stage:@"OpenXRay foreground after test-runner audio deactivation"])
        return;
    [NSThread sleepForTimeInterval:3.0];
    [self attachScreenNamed:@"audio-after-foreground-interruption"];
}

- (void)testReliabilityFiveAppSwitchCyclesAndForegroundAudioInterruption
{
    XCUIApplication* app = [self openXRayApplication];

    if (![self runFiveAppSwitchCyclesForOpenXRay:app attachmentPrefix:@"reliability"])
        return;
    if (![self startForegroundAudioInterruptionDriver])
        return;
    if (![self requireApplication:app
                    reachesState:XCUIApplicationStateRunningForeground
                          timeout:10.0
                            stage:@"OpenXRay foreground during reliability audio interruption"])
        return;

    [NSThread sleepForTimeInterval:3.0];
    [self stopForegroundAudioInterruptionDriver];
    if (![self requireApplication:app
                    reachesState:XCUIApplicationStateRunningForeground
                          timeout:30.0
                            stage:@"OpenXRay foreground after reliability audio interruption"])
        return;
    [NSThread sleepForTimeInterval:3.0];
    [self attachScreenNamed:@"reliability-after-foreground-audio-interruption"];
}
@end
