import Accelerate
import Foundation

/// Keeps the speaker monitor from howling when the mic is in the same room.
/// 1. Shifts the monitored sound up 5 Hz (single-sideband via a Hilbert filter): every trip
///    around the speaker→mic loop lands on a new frequency, so a howl cannot build up.
/// 2. Watches the spectrum; a narrow peak that stays put for ~170 ms gets a notch filter.
/// 3. A limiter caps the output whatever happens.
/// Runs on the audio thread: no allocation after init.
final class FeedbackGuard {
    let rate: Double
    var volume: Float = 0.5
    private(set) var caught = 0
    private(set) var notchCount = 0

    // Hilbert single-sideband shifter.
    private static let length = 255
    private let center = length/2
    private let taps: UnsafeMutablePointer<Float>
    private let history: UnsafeMutablePointer<Float>
    private var position = 0
    private var phase = 0.0
    private let phaseStep: Double

    // Notches: RBJ peaking filters with negative gain.
    private struct Notch { var frequency: Double; var depth: Double; var b0, b1, b2, a1, a2: Float; var z1: Float = 0, z2: Float = 0 }
    private var notches: [Notch] = []
    private let maxNotches = 8

    // Howl detector.
    private let fftSize = 2048, hop = 1024
    private let log2n: vDSP_Length = 11
    private let setup: FFTSetup
    private let ring: UnsafeMutablePointer<Float>
    private let frame: UnsafeMutablePointer<Float>
    private let window: UnsafeMutablePointer<Float>
    private let real: UnsafeMutablePointer<Float>, imaginary: UnsafeMutablePointer<Float>
    private let power: UnsafeMutablePointer<Float>
    private var ringPosition = 0, sinceAnalysis = 0
    private var candidate = -1, candidateFrames = 0
    private var candidatePower: Float = 0

    private var envelope: Float = 0

    init(rate: Double, shiftHz: Double = 5) {
        self.rate = rate
        phaseStep = 2*Double.pi*shiftHz/rate
        taps = .allocate(capacity:FeedbackGuard.length); history = .allocate(capacity:FeedbackGuard.length)
        history.initialize(repeating:0,count:FeedbackGuard.length)
        for m in 0..<FeedbackGuard.length {
            let k = m-center
            let blackman = 0.42-0.5*cos(2*Double.pi*Double(m)/Double(FeedbackGuard.length-1))+0.08*cos(4*Double.pi*Double(m)/Double(FeedbackGuard.length-1))
            taps[m] = k % 2 == 0 ? 0 : Float(2/(Double.pi*Double(k))*blackman)
        }
        setup = vDSP_create_fftsetup(log2n,FFTRadix(kFFTRadix2))!
        ring = .allocate(capacity:fftSize); ring.initialize(repeating:0,count:fftSize)
        frame = .allocate(capacity:fftSize); window = .allocate(capacity:fftSize)
        vDSP_hann_window(window,vDSP_Length(fftSize),Int32(vDSP_HANN_NORM))
        real = .allocate(capacity:fftSize/2); imaginary = .allocate(capacity:fftSize/2); power = .allocate(capacity:fftSize/2)
        notches.reserveCapacity(maxNotches)
    }
    deinit {
        vDSP_destroy_fftsetup(setup)
        for p in [taps,history,ring,frame,window,real,imaginary,power] { p.deallocate() }
    }

    /// Processes channel 0 of an interleaved buffer in place and copies it to the other channels.
    func process(_ buffer: UnsafeMutablePointer<Float>, frames: Int, channels: Int) {
        let n = FeedbackGuard.length
        for f in 0..<frames {
            var x = buffer[f*channels]*volume
            for i in 0..<notches.count {
                let y = notches[i].b0*x+notches[i].z1
                notches[i].z1 = notches[i].b1*x-notches[i].a1*y+notches[i].z2
                notches[i].z2 = notches[i].b2*x-notches[i].a2*y
                x = y
            }
            ring[ringPosition] = x; ringPosition = (ringPosition+1) % fftSize
            sinceAnalysis += 1
            if sinceAnalysis >= hop { sinceAnalysis = 0; analyse() }

            history[position] = x
            var hilbert: Float = 0
            var m = 1
            while m < n {
                hilbert += taps[m]*history[(position-m+n) % n]
                m += 2
            }
            let delayed = history[(position-center+n) % n]
            position = (position+1) % n
            var y = delayed*Float(cos(phase))-hilbert*Float(sin(phase))
            phase += phaseStep
            if phase > 2*Double.pi { phase -= 2*Double.pi }

            envelope = max(abs(y),envelope*0.9995)
            if envelope > 0.7 { y *= 0.7/envelope }
            for c in 0..<channels { buffer[f*channels+c] = y }
        }
    }

    private func analyse() {
        for i in 0..<fftSize { frame[i] = ring[(ringPosition+i) % fftSize]*window[i] }
        var split = DSPSplitComplex(realp:real,imagp:imaginary)
        frame.withMemoryRebound(to:DSPComplex.self,capacity:fftSize/2) { vDSP_ctoz($0,2,&split,1,vDSP_Length(fftSize/2)) }
        vDSP_fft_zrip(setup,&split,1,log2n,FFTDirection(FFT_FORWARD))
        vDSP_zvmags(&split,1,power,1,vDSP_Length(fftSize/2))
        let binHz = rate/Double(fftSize)
        let low = Int(150/binHz), high = min(fftSize/2-14,Int(8000/binHz))
        var peak = low
        for b in low...high where power[b] > power[peak] { peak = b }
        // A Hann-windowed full-scale sine peaks at (N/2)^2 in vDSP's scaling.
        let level = 10*log10(Double(power[peak])/Double(fftSize*fftSize/4)+1e-20)
        var around: Float = 0
        for d in 3...12 { around += power[peak-d]+power[peak+d] }
        around /= 20
        let ratio = 10*log10(Double(power[peak]/max(around,1e-20)))
        guard level > -50, ratio > 22 else { candidate = -1; candidateFrames = 0; return }
        if abs(peak-candidate) <= 1 && power[peak] >= candidatePower*0.6 { candidateFrames += 1 } else { candidate = peak; candidateFrames = 1 }
        candidatePower = power[peak]
        guard candidateFrames >= 8 else { return }
        let a = power[peak-1], b = power[peak], c = power[peak+1]
        let offset = 0.5*Double(a-c)/Double(a-2*b+c == 0 ? 1 : a-2*b+c)
        addNotch(at:(Double(peak)+offset)*binHz)
        candidate = -1; candidateFrames = 0
    }

    private func addNotch(at frequency: Double) {
        caught += 1
        if let i = notches.firstIndex(where:{ abs($0.frequency-frequency) < frequency*0.03 }) {
            notches[i] = design(frequency:notches[i].frequency,depth:min(30,notches[i].depth+6))
        } else {
            if notches.count == maxNotches { notches.removeFirst() }
            notches.append(design(frequency:frequency,depth:12))
        }
        notchCount = notches.count
    }

    private func design(frequency: Double, depth: Double) -> Notch {
        let a = pow(10,-depth/40), w = 2*Double.pi*frequency/rate, alpha = sin(w)/(2*30)
        let a0 = 1+alpha/a
        return Notch(frequency:frequency,depth:depth,b0:Float((1+alpha*a)/a0),b1:Float(-2*cos(w)/a0),b2:Float((1-alpha*a)/a0),a1:Float(-2*cos(w)/a0),a2:Float((1-alpha/a)/a0))
    }
}

/// Simulated room for the self-test: the speaker reaches the mic after 4 ms through a
/// resonance at 1.3 kHz with loop gain 1.5, which howls on its own. Returns the output
/// level over the last second and how many howls the guard caught.
func simulateRoom(guarded: Bool, shiftHz: Double = 5, seconds: Double = 4) -> (rms: Float, caught: Int) {
    let rate = 48000.0, delay = 192
    let guardian = FeedbackGuard(rate:rate,shiftHz:shiftHz); guardian.volume = 1
    var line = [Float](repeating:0,count:delay), at = 0
    let w = 2*Double.pi*1300/rate, alpha = sin(w)/12, a0 = 1+alpha
    let b0 = Float(alpha/a0), b2 = Float(-alpha/a0), a1 = Float(-2*cos(w)/a0), a2 = Float((1-alpha)/a0)
    var x1: Float = 0, x2: Float = 0, y1: Float = 0, y2: Float = 0
    var frame: [Float] = [0,0], tail: Float = 0, seed: UInt32 = 1
    let total = Int(seconds*rate)
    for n in 0..<total {
        let speaker = line[at]
        let band = b0*speaker+b2*x2-a1*y1-a2*y2
        x2 = x1; x1 = speaker; y2 = y1; y1 = band
        seed = seed &* 1664525 &+ 1013904223
        let noise = (Float(seed >> 8)/Float(1 << 24)-0.5)*0.002
        let mic = 1.5*band+0.1*speaker+noise
        var y: Float
        if guarded { frame[0] = mic; guardian.process(&frame,frames:1,channels:2); y = frame[0] }
        else { y = max(-0.7,min(0.7,mic)) }
        line[at] = y; at = (at+1) % delay
        if n >= total-Int(rate) { tail += y*y }
    }
    return ((tail/Float(rate)).squareRoot(),guardian.caught)
}

/// False alarms: steady signals that are not feedback must not be notched.
func guardFalseAlarms() -> Int {
    let rate = 48000.0
    let guardian = FeedbackGuard(rate:rate); guardian.volume = 1
    var seed: UInt32 = 7, frame: [Float] = [0,0]
    for n in 0..<Int(rate*3) {
        seed = seed &* 1664525 &+ 1013904223
        let noise = (Float(seed >> 8)/Float(1 << 24)-0.5)*0.2
        // a held vowel: 140 Hz sawtooth with slow vibrato, plus noise
        let t = Double(n)/rate
        let saw = Float(2*((t*140*(1+0.01*sin(2*Double.pi*5*t))).truncatingRemainder(dividingBy:1))-1)*0.3
        frame[0] = noise*(n < Int(rate*1.5) ? 1 : 0.1)+saw*(n < Int(rate*1.5) ? 0 : 1)
        guardian.process(&frame,frames:1,channels:2)
    }
    return guardian.caught
}
