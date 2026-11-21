// SPDX-License-Identifier: GPL-2.0-or-later

import UIKit

/// A native selectable snapshot of the terminal's current scroll position.
/// Scrolling the terminal before a long press selects from its history too.
@MainActor
final class ConsoleTextSelectionController: UIViewController {
    private let contents: String
    private let initialRange: NSRange?
    let textView = UITextView()

    init(text: String, range: NSRange?) {
        contents = text
        initialRange = range
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Select console text"
        view.backgroundColor = .systemBackground
        textView.text = contents
        textView.isEditable = false
        textView.isSelectable = true
        textView.alwaysBounceVertical = true
        textView.font = .monospacedSystemFont(ofSize: 15, weight: .regular)
        textView.accessibilityIdentifier = "consoleSelectionText"
        textView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(textView)
        NSLayoutConstraint.activate([
            textView.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor),
            textView.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor),
            textView.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            textView.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor),
        ])
        navigationItem.rightBarButtonItem = UIBarButtonItem(
            barButtonSystemItem: .done, target: self, action: #selector(done)
        )
        navigationItem.leftBarButtonItem = UIBarButtonItem(
            title: "Copy selection", style: .plain, target: self, action: #selector(copySelection)
        )
        navigationItem.leftBarButtonItem?.accessibilityIdentifier = "consoleCopySelection"
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        textView.becomeFirstResponder()
        if let range = initialRange, NSMaxRange(range) <= (contents as NSString).length {
            textView.selectedRange = range
            textView.scrollRangeToVisible(range)
        }
    }

    @objc private func done() { dismiss(animated: true) }
    @objc func copySelection() { textView.copy(nil) }
}
