import AVFoundation
import SwiftUI
import UIKit

/// Camera QR scanner for the pairing code. Only paihealth://pair links are accepted.
struct QRScannerView: UIViewControllerRepresentable {
    let onCode: (String) -> Void

    func makeUIViewController(context: Context) -> ScannerController {
        let c = ScannerController()
        c.onCode = onCode
        return c
    }

    func updateUIViewController(_ controller: ScannerController, context: Context) {}
}

final class ScannerController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    var onCode: ((String) -> Void)?
    private let session = AVCaptureSession()
    private var done = false
    private let note = UILabel()

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        note.textColor = .white
        note.numberOfLines = 0
        note.textAlignment = .center
        note.text = "Point the camera at the code on your computer."
        note.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(note)
        NSLayoutConstraint.activate([
            note.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 24),
            note.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -24),
            note.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -32),
        ])
        AVCaptureDevice.requestAccess(for: .video) { ok in
            DispatchQueue.main.async {
                if ok { self.start() } else { self.note.text = "Camera access is off. Paste the pairing link instead." }
            }
        }
    }

    private func start() {
        guard let cam = AVCaptureDevice.default(for: .video), let input = try? AVCaptureDeviceInput(device: cam),
              session.canAddInput(input) else {
            note.text = "No camera available. Paste the pairing link instead."
            return
        }
        session.addInput(input)
        let out = AVCaptureMetadataOutput()
        guard session.canAddOutput(out) else { return }
        session.addOutput(out)
        out.setMetadataObjectsDelegate(self, queue: .main)
        out.metadataObjectTypes = [.qr]
        let layer = AVCaptureVideoPreviewLayer(session: session)
        layer.frame = view.bounds
        layer.videoGravity = .resizeAspectFill
        view.layer.insertSublayer(layer, at: 0)
        DispatchQueue.global(qos: .userInitiated).async { self.session.startRunning() }
    }

    func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput objects: [AVMetadataObject], from connection: AVCaptureConnection) {
        guard !done, let code = (objects.first as? AVMetadataMachineReadableCodeObject)?.stringValue,
              code.hasPrefix("paihealth://pair?") else { return }
        done = true
        session.stopRunning()
        onCode?(code)
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        if session.isRunning { session.stopRunning() }
    }
}
