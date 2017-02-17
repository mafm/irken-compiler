;; -*- Mode: Irken -*-

;; Tak, the Hideous New Girl!

(define (>= a b)
  (%llicmp uge a b))

(define (- a b)
  (%llarith sub a b))

(define (zero? a)
  (%llicmp eq a 0))

(define (tak x y z)
  (if (>= y x)
      z
      (tak (tak (- x 1) y z)
	   (tak (- y 1) z x)
	   (tak (- z 1) x y))))

(let loop ((n 20))
  (let ((r (tak 18 12 6)))
    (if (zero? n)
	r
	(loop (- n 1)))))
